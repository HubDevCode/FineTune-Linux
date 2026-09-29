// FineTune Linux — backend PipeWire.
//
// Implementazione del trait `AudioBackend` (finetune-core) su libpipewire.
//
// Modello: connessione sincrona per operazione. Ogni chiamata apre un
// mainloop, si connette al daemon, esegue l'operazione e chiude. Non c'è
// stato persistente tra le chiamate, quindi la struttura è "zero-sized".
//
// Semantica volume (allineata a WirePlumber `module-mixer-api.c`):
//   - il valore "display" è la radice cubica del volume lineare per canale
//     (scala CUBIC); il nodo espone `channelVolumes` (vettore di float
//     lineari per canale) nel parametro `Props` (chiave SPA 0x10008);
//   - set:  scrive `channelVolumes[i] = display^3` (Props del nodo);
//   - get:  legge `channelVolumes[0]` e restituisce `cbrt(...)`.
// Il sink di default è memorizzato nel metadata `default` (chiave
// `default.audio.sink`, tipo `Spa:String:JSON`, valore `{"name": ...}`).

use finetune_core::backend::{AudioBackend, AudioInput, AudioSink, AudioStream, BackendError};
use pipewire as pw;
use pw::spa::{
    param::ParamType,
    pod::{deserialize::PodDeserializer, serialize::PodSerializer, Object, Pod, Property, Value, ValueArray},
    utils::SpaTypes,
};
use std::{
    cell::RefCell,
    collections::HashMap,
    io::Cursor,
    process::Command,
    rc::Rc,
    time::Duration,
};

/// Backend PipeWire. Zero-campi: ogni operazione apre una connessione.
pub struct PipeWireBackend;

const KEY_MUTE: u32 = 0x10004; // SPA_PROP_mute
const KEY_CHANNEL_VOLUMES: u32 = 0x10008; // SPA_PROP_channelVolumes

const META_NAME_DEFAULT: &str = "default";
const KEY_DEFAULT_SINK: &str = "default.audio.sink";
const KEY_DEFAULT_SOURCE: &str = "default.audio.source";

fn to_io(e: impl std::fmt::Display) -> BackendError {
    BackendError::Io(e.to_string())
}

/// Apre (brevemente) una sessione verso il daemon e la usa per `f`.
fn with_session<T>(
    f: impl FnOnce(&pw::core::CoreRc, &pw::main_loop::MainLoopRc) -> Result<T, BackendError>,
) -> Result<T, BackendError> {
    pw::init();
    let mainloop = pw::main_loop::MainLoopRc::new(None).map_err(to_io)?;
    let context = pw::context::ContextRc::new(&mainloop, None).map_err(to_io)?;
    let core = context.connect_rc(None).map_err(to_io)?;
    f(&core, &mainloop)
}

/// Registra un listener sul `done` del core sincronizzato.
fn arm_sync_done(core: &pw::core::CoreRc, done: &Rc<RefCell<bool>>) -> pw::core::Listener {
    let pending = core.sync(0).expect("core.sync");
    let d = done.clone();
    core.add_listener_local()
        .done(move |id, seq| {
            if id == pw::core::PW_ID_CORE && seq == pending {
                *d.borrow_mut() = true;
            }
        })
        .register()
}

/// Fa girare il loop finché `flag` non diventa true (o scatta il timeout).
fn iterate_until(mainloop: &pw::main_loop::MainLoopRc, flag: &Rc<RefCell<bool>>) -> Result<(), BackendError> {
    let mut n = 0;
    while !*flag.borrow() && n < 1000 {
        mainloop
            .loop_()
            .iterate(pw::loop_::Timeout::Finite(Duration::from_millis(10)));
        n += 1;
    }
    if !*flag.borrow() {
        return Err(BackendError::Timeout);
    }
    Ok(())
}

/// Come `iterate_until` ma per uno slot `Option`: si ferma quando valorizzato.
fn iterate_until_option<T>(
    mainloop: &pw::main_loop::MainLoopRc,
    slot: &Rc<RefCell<Option<T>>>,
) -> Result<(), BackendError> {
    let mut n = 0;
    while slot.borrow().is_none() && n < 1000 {
        mainloop
            .loop_()
            .iterate(pw::loop_::Timeout::Finite(Duration::from_millis(10)));
        n += 1;
    }
    if slot.borrow().is_none() {
        return Err(BackendError::Timeout);
    }
    Ok(())
}

/// Global scoperto durante l'enumerazione del registry.
#[derive(Debug)]
struct Global {
    id: u32,
    type_: pw::types::ObjectType,
    props: HashMap<String, String>,
}

/// Contenitore che tiene vivi i proxy e i listener PipeWire durante
/// l'iterazione sincrona di un'operazione.
#[derive(Default)]
struct KeepAlive {
    proxies: Vec<Box<dyn pw::proxy::ProxyT>>,
    listeners: Vec<Box<dyn pw::proxy::Listener>>,
}

type KeepAliveRc = Rc<RefCell<KeepAlive>>;

/// Enumera i global (nodi, link, metadata…) del registry.
fn collect_globals(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
) -> Result<Vec<Global>, BackendError> {
    let registry = core.get_registry_rc().map_err(to_io)?;
    let globals: Rc<RefCell<Vec<Global>>> = Rc::new(RefCell::new(Vec::new()));
    let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let _core_listener = arm_sync_done(core, &done);
    let globals2 = globals.clone();
    let _reg_listener = registry
        .add_listener_local()
        .global(move |obj| {
            let mut props = HashMap::new();
            if let Some(p) = &obj.props {
                for (k, v) in p.iter() {
                    props.insert(k.to_string(), v.to_string());
                }
            }
            globals2.borrow_mut().push(Global {
                id: obj.id,
                type_: obj.type_.clone(),
                props,
            });
        })
        .register();
    iterate_until(mainloop, &done)?;
    let mut out = Vec::new();
    std::mem::swap(&mut out, &mut globals.borrow_mut());
    out.retain(|g| !g.props.is_empty() || g.type_ == pw::types::ObjectType::Node);
    Ok(out)
}

// ---------------------------------------------------------------------------
// Parametro Props dei nodi
// ---------------------------------------------------------------------------

/// Risultato della lettura del parametro Props di un nodo audio.
#[derive(Debug, Clone, Copy)]
struct NodeProps {
    /// volume "display" (= cbrt del lineare), scala WirePlumber CUBIC
    display_volume: f32,
    boost: f32,
    muted: bool,
    channels: usize,
}

/// Lettura completa di un nodo: info (dict props) + parametro Props.
#[derive(Debug)]
struct NodeRead {
    props: Option<NodeProps>,
    info_props: HashMap<String, String>,
}

/// Estrae il volume/mute dal pod Props, restituendo `Some` solo se il pod
/// contiene `channelVolumes` (è il pod "mixer" del nodo, non quello ALSA).
fn parse_props_pod(pod: &Pod) -> Option<NodeProps> {
    let (_, value) = PodDeserializer::deserialize_from::<Value>(pod.as_bytes()).ok()?;
    let Value::Object(obj) = value else {
        return None;
    };
    let mut ch_vols: Vec<f32> = Vec::new();
    let mut muted = false;
    for prop in &obj.properties {
        match prop.key {
            KEY_MUTE => {
                if let Value::Bool(b) = &prop.value {
                    muted = *b;
                }
            }
            KEY_CHANNEL_VOLUMES => {
                if let Value::ValueArray(ValueArray::Float(arr)) = &prop.value {
                    ch_vols = arr.clone();
                }
            }
            _ => {}
        }
    }
    if ch_vols.is_empty() {
        return None;
    }
    let linear = ch_vols[0];
    // Se il livello reso supera 1, assumiamo che sia dovuto al boost (v³·b, con v=1).
    let (display_volume, boost) = if linear > 1.0 {
        (1.0, linear)
    } else {
        (linear.cbrt(), 1.0)
    };
    Some(NodeProps {
        display_volume,
        boost,
        muted,
        channels: ch_vols.len(),
    })
}

/// Legge info completo + parametro Props del nodo (subscribe + enum + attesa).
fn read_node_props(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
    node_id: u32,
) -> Result<Option<NodeRead>, BackendError> {
    let registry = core.get_registry_rc().map_err(to_io)?;
    let out: Rc<RefCell<Option<NodeRead>>> = Rc::new(RefCell::new(None));
    let keep: KeepAliveRc = Rc::new(RefCell::new(KeepAlive::default()));
    let _reg_listener = registry
        .add_listener_local()
        .global({
            let out = out.clone();
            let keep = keep.clone();
            let registry_weak = registry.clone().downgrade();
            move |obj| {
                if obj.type_ != pw::types::ObjectType::Node || obj.id != node_id {
                    return;
                }
                let Some(reg) = registry_weak.upgrade() else {
                    return;
                };
                let node: pw::node::Node = match reg.bind(obj) {
                    Ok(n) => n,
                    Err(_) => return,
                };
                let out2 = out.clone();
                let out3 = out2.clone();
                let node_listener = node
                    .add_listener_local()
                    .info(move |info| {
                        let mut props = HashMap::new();
                        if let Some(d) = info.props() {
                            for (k, v) in d.iter() {
                                props.insert(k.to_string(), v.to_string());
                            }
                        }
                        let mut o = out2.borrow_mut();
                        let entry = o.get_or_insert_with(|| NodeRead {
                            props: None,
                            info_props: HashMap::new(),
                        });
                        entry.info_props = props;
                    })
                    .param(move |_seq, _id, _index, _next, param| {
                        let Some(pod) = param else {
                            return;
                        };
                        if let Some(nps) = parse_props_pod(pod) {
                            let mut o = out3.borrow_mut();
                            let entry = o.get_or_insert_with(|| NodeRead {
                                props: None,
                                info_props: HashMap::new(),
                            });
                            entry.props = Some(nps);
                        }
                    })
                    .register();
                node.subscribe_params(&[ParamType::Props]);
                node.enum_params(1, Some(ParamType::Props), 0, u32::MAX);
                {
                    let mut k = keep.borrow_mut();
                    k.proxies.push(Box::new(node));
                    k.listeners.push(Box::new(node_listener));
                }
            }
        })
        .register();

    match iterate_until_option(mainloop, &out) {
        Ok(()) => Ok(out.borrow_mut().take()),
        Err(_) => Ok(None),
    }
}

/// Serializza un oggetto Props (channelVolumes e/o mute) in un Pod.
fn build_props_pod(channel_volumes: &[f32], mute: Option<bool>) -> Vec<u8> {
    let mut properties: Vec<Property> = Vec::new();
    if !channel_volumes.is_empty() {
        properties.push(Property::new(
            KEY_CHANNEL_VOLUMES,
            Value::ValueArray(ValueArray::Float(channel_volumes.to_vec())),
        ));
    }
    if let Some(m) = mute {
        properties.push(Property::new(KEY_MUTE, Value::Bool(m)));
    }
    let obj = Object {
        type_: SpaTypes::ObjectParamProps.as_raw(),
        id: ParamType::Props.as_raw(),
        properties,
    };
    let (out, _) = PodSerializer::serialize(Cursor::new(Vec::new()), &Value::Object(obj)).unwrap();
    out.into_inner()
}

/// Invia un set_param dell'oggetto Props al nodo.
fn set_node_props(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
    node_id: u32,
    bytes: Vec<u8>,
) -> Result<(), BackendError> {
    let registry = core.get_registry_rc().map_err(to_io)?;
    let sent: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let keep: KeepAliveRc = Rc::new(RefCell::new(KeepAlive::default()));
    let _reg_listener = registry
        .add_listener_local()
        .global({
            let sent = sent.clone();
            let keep = keep.clone();
            let registry_weak = registry.clone().downgrade();
            move |obj| {
                if obj.type_ != pw::types::ObjectType::Node || obj.id != node_id {
                    return;
                }
                let Some(reg) = registry_weak.upgrade() else {
                    return;
                };
                let node: pw::node::Node = match reg.bind(obj) {
                    Ok(n) => n,
                    Err(_) => return,
                };
                if let Some(pod) = Pod::from_bytes(bytes.as_slice()) {
                    node.set_param(ParamType::Props, 0, pod);
                }
                *sent.borrow_mut() = true;
                let node_listener = node.add_listener_local().info(|_| {}).register();
                {
                    let mut k = keep.borrow_mut();
                    k.proxies.push(Box::new(node));
                    k.listeners.push(Box::new(node_listener));
                }
            }
        })
        .register();

    match iterate_until(mainloop, &sent) {
        Ok(()) => {}
        Err(BackendError::Timeout) => {
            return Err(BackendError::DeviceNotFound(node_id.to_string()))
        }
        Err(e) => return Err(e),
    }
    // il set_param è stato inviato sulla rete; sincronizziamoci col server
    // cosicché venga elaborato PRIMA che chiudiamo la sessione.
    let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let _core_listener = arm_sync_done(core, &done);
    iterate_until(mainloop, &done)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Sink e stream
// ---------------------------------------------------------------------------

fn media_class(g: &Global) -> &str {
    g.props.get("media.class").map(String::as_str).unwrap_or("")
}

/// Scrive volume base + boost (moltiplicatore lineare 1…4) sul nodo.
fn write_node_volume(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
    node_id: u32,
    volume: f32,
    boost: f32,
) -> Result<(), BackendError> {
    let channels = read_node_props(core, mainloop, node_id)?
        .and_then(|n| n.props)
        .map(|p| p.channels)
        .unwrap_or(2);
    let v = volume.clamp(0.0, 1.0);
    let linear = v * v * v * boost.clamp(1.0, 4.0);
    let bytes = build_props_pod(&vec![linear; channels], None);
    set_node_props(core, mainloop, node_id, bytes)
}

/// Crea un link persistente (object.linger) tra un nodo di output e un sink.
fn create_link(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
    output_node: u32,
    input_node: u32,
) -> Result<(), BackendError> {
    let props = pw::properties::properties! {
        "link.output.node" => output_node.to_string(),
        "link.input.node" => input_node.to_string(),
        "object.linger" => "1",
    };
    let link: pw::link::Link = core.create_object("link-factory", &props).map_err(to_io)?;
    let keep: KeepAliveRc = Rc::new(RefCell::new(KeepAlive::default()));
    keep.borrow_mut().proxies.push(Box::new(link));
    let _keep = keep;
    let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let _core_listener = arm_sync_done(core, &done);
    iterate_until(mainloop, &done)?;
    Ok(())
}

/// Distrugge i link con gli id indicati bindando il global e chiamando
/// `destroy_object` (necessario per i link con object.linger creati da altri).
fn destroy_links(
    core: &pw::core::CoreRc,
    mainloop: &pw::main_loop::MainLoopRc,
    link_ids: &[u32],
) -> Result<(), BackendError> {
    if link_ids.is_empty() {
        return Ok(());
    }
    let ids: std::collections::HashSet<u32> = link_ids.iter().copied().collect();
    let registry = core.get_registry_rc().map_err(to_io)?;
    let found: Rc<RefCell<Vec<pw::link::Link>>> = Rc::new(RefCell::new(Vec::new()));
    let _reg_listener = registry
        .add_listener_local()
        .global({
            let ids = ids.clone();
            let found = found.clone();
            let registry_weak = registry.clone().downgrade();
            move |obj| {
                if obj.type_ != pw::types::ObjectType::Link || !ids.contains(&obj.id) {
                    return;
                }
                let Some(reg) = registry_weak.upgrade() else {
                    return;
                };
                if let Ok(link) = reg.bind(obj) {
                    found.borrow_mut().push(link);
                }
            }
        })
        .register();

    // Assicura che l'enumerazione del registry sia completa prima di distruggere.
    let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let _core_listener = arm_sync_done(core, &done);
    iterate_until(mainloop, &done)?;

    let links = found.borrow_mut().drain(..).collect::<Vec<_>>();
    for link in links {
        let _ = core.destroy_object(link);
    }
    let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
    let _core_listener = arm_sync_done(core, &done);
    iterate_until(mainloop, &done)?;
    Ok(())
}

impl PipeWireBackend {
    pub fn new() -> Self {
        Self
    }

    /// Ritorna il nome del nodo di default (sink o source) dal metadata.
    fn default_device_name(
        core: &pw::core::CoreRc,
        mainloop: &pw::main_loop::MainLoopRc,
        key: &'static str,
    ) -> Result<Option<String>, BackendError> {
        let registry = core.get_registry_rc().map_err(to_io)?;
        let found: Rc<RefCell<Option<String>>> = Rc::new(RefCell::new(None));
        let keep: KeepAliveRc = Rc::new(RefCell::new(KeepAlive::default()));
        let key2 = key;
        let _reg_listener = registry
            .add_listener_local()
            .global({
                let found = found.clone();
                let keep = keep.clone();
                let registry_weak = registry.clone().downgrade();
                move |obj| {
                    if obj.type_ != pw::types::ObjectType::Metadata {
                        return;
                    }
                    let Some(props) = &obj.props else {
                        return;
                    };
                    if props.get("metadata.name") != Some(META_NAME_DEFAULT) {
                        return;
                    }
                    let Some(reg) = registry_weak.upgrade() else {
                        return;
                    };
                    let meta: pw::metadata::Metadata = match reg.bind(obj) {
                        Ok(m) => m,
                        Err(_) => return,
                    };
                    let found2 = found.clone();
                    let listener = meta
                        .add_listener_local()
                        .property(move |_subject, k, _type_, value| {
                            if k == Some(key2) {
                                if let Some(v) = value {
                                    if let Ok(json) =
                                        serde_json::from_str::<serde_json::Value>(&v.to_string())
                                    {
                                        if let Some(name) = json.get("name").and_then(|n| n.as_str())
                                        {
                                            *found2.borrow_mut() = Some(name.to_string());
                                        }
                                    }
                                }
                            }
                            0
                        })
                        .register();
                    {
                        let mut k = keep.borrow_mut();
                        k.proxies.push(Box::new(meta));
                        k.listeners.push(Box::new(listener));
                    }
                }
            })
            .register();

        let mut n = 0;
        while found.borrow().is_none() && n < 100 {
            mainloop
                .loop_()
                .iterate(pw::loop_::Timeout::Finite(Duration::from_millis(20)));
            n += 1;
        }
        let result = found.borrow_mut().take();
        Ok(result)
    }

    fn set_default_device_name(
        core: &pw::core::CoreRc,
        mainloop: &pw::main_loop::MainLoopRc,
        key: &'static str,
        name: &str,
    ) -> Result<(), BackendError> {
        let name = name.to_string();
        let registry = core.get_registry_rc().map_err(to_io)?;
        let applied: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
        let keep: KeepAliveRc = Rc::new(RefCell::new(KeepAlive::default()));
        let key2 = key;
        let _reg_listener = registry
            .add_listener_local()
            .global({
                let applied = applied.clone();
                let keep = keep.clone();
                let registry_weak = registry.clone().downgrade();
                move |obj| {
                    if obj.type_ != pw::types::ObjectType::Metadata {
                        return;
                    }
                    let Some(props) = &obj.props else {
                        return;
                    };
                    if props.get("metadata.name") != Some(META_NAME_DEFAULT) {
                        return;
                    }
                    let Some(reg) = registry_weak.upgrade() else {
                        return;
                    };
                    let meta: pw::metadata::Metadata = match reg.bind(obj) {
                        Ok(m) => m,
                        Err(_) => return,
                    };
                    let json = format!(r#"{{"name":"{name}"}}"#);
                    meta.set_property(0, key2, Some("Spa:String:JSON"), Some(&json));
                    *applied.borrow_mut() = true;
                    let listener = meta.add_listener_local().property(|_, _, _, _| 0).register();
                    {
                        let mut k = keep.borrow_mut();
                        k.proxies.push(Box::new(meta));
                        k.listeners.push(Box::new(listener));
                    }
                }
            })
            .register();

        // Attende la scrittura e la sincronizza col server (ack) prima di uscire.
        match iterate_until(mainloop, &applied) {
            Ok(()) => {}
            Err(BackendError::Timeout) => {
                return Err(BackendError::DeviceNotFound("default metadata".to_string()))
            }
            Err(e) => return Err(e),
        }
        let done: Rc<RefCell<bool>> = Rc::new(RefCell::new(false));
        let _core_listener = arm_sync_done(core, &done);
        iterate_until(mainloop, &done)?;
        Ok(())
    }
}

impl Default for PipeWireBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl AudioBackend for PipeWireBackend {
    type Error = BackendError;

    fn list_sinks(&self) -> Result<Vec<AudioSink>, BackendError> {
        with_session(|core, mainloop| {
            let globals = collect_globals(core, mainloop)?;
            let default_name = Self::default_device_name(core, mainloop, KEY_DEFAULT_SINK)?;

            let mut sinks = Vec::new();
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Node || media_class(g) != "Audio/Sink" {
                    continue;
                }
                let node_id = g.id;
                let name = g.props.get("node.name").cloned().unwrap_or_default();
                let description = g
                    .props
                    .get("node.description")
                    .cloned()
                    .filter(|d| !d.is_empty())
                    .unwrap_or_else(|| name.clone());
                let node_read = read_node_props(core, mainloop, node_id)?;
                let (display_volume, muted, channels) = match node_read.and_then(|n| n.props) {
                    Some(p) => (p.display_volume, p.muted, p.channels),
                    None => {
                        // nessun Props utile: fallback dal dict del global
                        let ch = g
                            .props
                            .get("audio.channels")
                            .and_then(|c| c.parse::<u32>().ok())
                            .unwrap_or(2);
                        (1.0, false, ch as usize)
                    }
                };
                sinks.push(AudioSink {
                    id: node_id.to_string(),
                    node_id,
                    name: name.clone(),
                    description,
                    channels: channels as u32,
                    sample_rate: 48000,
                    volume: display_volume,
                    muted,
                    is_default: Some(&name) == default_name.as_ref(),
                    transport_hint: "pipewire".to_string(),
                });
            }
            sinks.sort_by_key(|s| s.node_id);
            Ok(sinks)
        })
    }

    fn list_inputs(&self) -> Result<Vec<AudioInput>, BackendError> {
        with_session(|core, mainloop| {
            let globals = collect_globals(core, mainloop)?;
            let default_name = Self::default_device_name(core, mainloop, KEY_DEFAULT_SOURCE)?;

            let mut inputs = Vec::new();
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Node || media_class(g) != "Audio/Source" {
                    continue;
                }
                let node_id = g.id;
                let name = g.props.get("node.name").cloned().unwrap_or_default();
                // Gli "echo" dei sink (monitor) non sono dispositivi di ingresso reali.
                if name.ends_with(".monitor") {
                    continue;
                }
                let description = g
                    .props
                    .get("node.description")
                    .cloned()
                    .filter(|d| !d.is_empty())
                    .unwrap_or_else(|| name.clone());
                let node_read = read_node_props(core, mainloop, node_id)?;
                let (display_volume, muted, channels) = match node_read.and_then(|n| n.props) {
                    Some(p) => (p.display_volume, p.muted, p.channels),
                    None => {
                        let ch = g
                            .props
                            .get("audio.channels")
                            .and_then(|c| c.parse::<u32>().ok())
                            .unwrap_or(1);
                        (1.0, false, ch as usize)
                    }
                };
                inputs.push(AudioInput {
                    id: node_id.to_string(),
                    node_id,
                    name: name.clone(),
                    description,
                    channels: channels as u32,
                    sample_rate: 48000,
                    volume: display_volume,
                    muted,
                    is_default: Some(&name) == default_name.as_ref(),
                    transport_hint: "pipewire".to_string(),
                });
            }
            inputs.sort_by_key(|s| s.node_id);
            Ok(inputs)
        })
    }

    fn list_streams(&self) -> Result<Vec<AudioStream>, BackendError> {
        with_session(|core, mainloop| {
            let globals = collect_globals(core, mainloop)?;

            // Mappa: node di stream -> set di sink collegati (via link).
            let sink_ids: std::collections::HashSet<u32> = globals
                .iter()
                .filter(|g| g.type_ == pw::types::ObjectType::Node && media_class(g) == "Audio/Sink")
                .map(|g| g.id)
                .collect();
            let mut links: HashMap<u32, Vec<u32>> = HashMap::new();
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Link {
                    continue;
                }
                let (output, input) = (
                    g.props.get("link.output.node").and_then(|v| v.parse::<u32>().ok()),
                    g.props.get("link.input.node").and_then(|v| v.parse::<u32>().ok()),
                );
                if let (Some(out), Some(inp)) = (output, input) {
                    if sink_ids.contains(&inp) {
                        links.entry(out).or_default().push(inp);
                    }
                }
            }

            let mut streams = Vec::new();
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Node {
                    continue;
                }
                let mc = media_class(g);
                if mc != "Stream/Output/Audio" {
                    continue;
                }
                let node_id = g.id;
                let node_read = read_node_props(core, mainloop, node_id)?;
                let info_props = node_read
                    .as_ref()
                    .map(|n| &n.info_props)
                    .unwrap_or(&g.props);
                let app_name = info_props
                    .get("application.name")
                    .cloned()
                    .unwrap_or_default();
                let media_name = info_props
                    .get("media.name")
                    .cloned()
                    .or_else(|| info_props.get("node.name").cloned())
                    .unwrap_or_default();
                let pid = info_props
                    .get("application.process.id")
                    .and_then(|p| p.parse::<u32>().ok())
                    .unwrap_or(0);

                let (display_volume, boost, muted) = match node_read.and_then(|n| n.props) {
                    Some(p) => (p.display_volume, p.boost, p.muted),
                    None => (1.0, 1.0, false),
                };

                let linked = links.get(&node_id).cloned().unwrap_or_default();
                streams.push(AudioStream {
                    id: node_id.to_string(),
                    node_id,
                    pid,
                    app_name,
                    media_name,
                    volume: display_volume,
                    boost,
                    muted,
                    linked_sink_ids: linked,
                });
            }
            streams.sort_by_key(|s| s.node_id);
            Ok(streams)
        })
    }

    fn set_sink_volume(&self, id: &str, volume: f32) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::DeviceNotFound(id.to_string()))?;
        with_session(|core, mainloop| write_node_volume(core, mainloop, node_id, volume, 1.0))
    }

    fn set_sink_mute(&self, id: &str, muted: bool) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::DeviceNotFound(id.to_string()))?;
        with_session(|core, mainloop| {
            let bytes = build_props_pod(&[], Some(muted));
            set_node_props(core, mainloop, node_id, bytes)
        })
    }

    fn set_input_volume(&self, id: &str, volume: f32) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::DeviceNotFound(id.to_string()))?;
        with_session(|core, mainloop| write_node_volume(core, mainloop, node_id, volume, 1.0))
    }

    fn set_input_mute(&self, id: &str, muted: bool) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::DeviceNotFound(id.to_string()))?;
        with_session(|core, mainloop| {
            let bytes = build_props_pod(&[], Some(muted));
            set_node_props(core, mainloop, node_id, bytes)
        })
    }

    fn set_stream_volume_with_boost(
        &self,
        id: &str,
        volume: f32,
        boost: f32,
    ) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::StreamNotFound(id.to_string()))?;
        with_session(|core, mainloop| {
            write_node_volume(core, mainloop, node_id, volume, boost)
        })
    }

    fn set_stream_mute(&self, id: &str, muted: bool) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::StreamNotFound(id.to_string()))?;
        with_session(|core, mainloop| {
            let bytes = build_props_pod(&[], Some(muted));
            set_node_props(core, mainloop, node_id, bytes)
        })
    }

    fn set_stream_links(&self, id: &str, sink_ids: &[String]) -> Result<(), BackendError> {
        let node_id: u32 = id.parse().map_err(|_| BackendError::StreamNotFound(id.to_string()))?;

        // Percorso principale: spostare la "sink-input" PulseAudio del client
        // (pipewire-pulse) con `pactl move-sink-input`. Il session manager lo
        // riconosce, lo mantiene e resta sincronizzato con il sistema. Funziona
        // per un solo sink; se fallisce o è assente, ripieghiamo sui link manuali.
        if sink_ids.len() == 1 {
            if let Some(target) = sink_ids.first() {
                match self.try_pa_move(node_id, target) {
                    Ok(true) => return Ok(()),
                    Ok(false) => {}
                    Err(e) => return Err(e), // errore reale di pactl → segnala
                }
            }
        }

        with_session(|core, mainloop| {
            let globals = collect_globals(core, mainloop)?;
            let wanted: std::collections::HashSet<u32> =
                sink_ids.iter().filter_map(|s| s.parse().ok()).collect();

            let mut to_destroy: Vec<u32> = Vec::new();
            let mut already: std::collections::HashSet<u32> = std::collections::HashSet::new();
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Link {
                    continue;
                }
                let out = g
                    .props
                    .get("link.output.node")
                    .and_then(|v| v.parse::<u32>().ok());
                if out != Some(node_id) {
                    continue;
                }
                let Some(inp) = g
                    .props
                    .get("link.input.node")
                    .and_then(|v| v.parse::<u32>().ok())
                else {
                    continue;
                };
                if wanted.contains(&inp) {
                    already.insert(inp);
                } else {
                    to_destroy.push(g.id);
                }
            }
            destroy_links(core, mainloop, &to_destroy)?;
            for wanted_id in &wanted {
                if !already.contains(wanted_id) {
                    create_link(core, mainloop, node_id, *wanted_id)?;
                }
            }
            Ok(())
        })
    }

    fn set_default_sink(&self, id: &str) -> Result<(), BackendError> {
        with_session(|core, mainloop| {
            let name = Self::resolve_node_name(core, mainloop, id)?;
            Self::set_default_device_name(core, mainloop, KEY_DEFAULT_SINK, &name)
        })
    }

    fn set_default_input(&self, id: &str) -> Result<(), BackendError> {
        with_session(|core, mainloop| {
            let name = Self::resolve_node_name(core, mainloop, id)?;
            Self::set_default_device_name(core, mainloop, KEY_DEFAULT_SOURCE, &name)
        })
    }
}

impl PipeWireBackend {
    /// Normalizza un id: numero (node id) → il relativo node.name, altrimenti assume sia un nome.
    fn resolve_node_name(
        core: &pw::core::CoreRc,
        mainloop: &pw::main_loop::MainLoopRc,
        id: &str,
    ) -> Result<String, BackendError> {
        if let Ok(nid) = id.parse::<u32>() {
            let globals = collect_globals(core, mainloop)?;
            globals
                .iter()
                .find(|g| g.type_ == pw::types::ObjectType::Node && g.id == nid)
                .and_then(|g| g.props.get("node.name").cloned())
                .ok_or_else(|| BackendError::DeviceNotFound(nid.to_string()))
        } else {
            Ok(id.to_string())
        }
    }

    /// Prova a instradare lo stream tramite il percorso PulseAudio
    /// (`pactl move-sink-input`), che WirePlumber/pipewire-pulse gestiscono e
    /// mantengono. Ritorna:
    ///  - `Ok(true)`  → spostamento eseguito con successo;
    ///  - `Ok(false)` → non applicabile (pactl assente, stream non visto da PA,
    ///    move rifiutato) → chiamante usa il fallback a link manuali;
    ///  - `Err(_)`     → errore da segnalare.
    fn try_pa_move(&self, node_id: u32, sink_ref: &str) -> Result<bool, BackendError> {
        let stream_name = with_session(|core, mainloop| {
            Ok(read_node_props(core, mainloop, node_id)?
                .map(|n| {
                    (
                        n.info_props.get("node.name").cloned().unwrap_or_default(),
                        n.info_props
                            .get("application.name")
                            .cloned()
                            .unwrap_or_default(),
                        n.info_props
                            .get("application.process.id")
                            .cloned()
                            .unwrap_or_default(),
                    )
                })
                .unwrap_or_default())
        })?;

        if !is_pactl_available() {
            return Ok(false);
        }

        let output = match Command::new("pactl").arg("list").arg("sink-inputs").output() {
            Ok(out) => out,
            Err(_) => return Ok(false),
        };
        // indice PA della sink-input, trovato per PID oppure node.name/app.name.
        // NB: i blocchi "Sink Input #" non sono separati da righe vuote: si
        // finalizza il blocco corrente quando parte il successivo.
        let mut index: Option<String> = None;
        let mut head: Option<String> = None;
        let mut pid: Option<String> = None;
        let mut name: Option<String> = None;
        let mut app: Option<String> = None;
        'outer: for line in String::from_utf8_lossy(&output.stdout).lines() {
            let line = line.trim();
            if let Some(rest) = line.strip_prefix("Sink Input #") {
                if let Some(h) = head.take() {
                    if pa_sink_input_matches(&h, &pid, &name, &app, &stream_name) {
                        index = Some(h);
                        break 'outer;
                    }
                }
                head = Some(rest.trim().to_string());
                pid = None;
                name = None;
                app = None;
                continue;
            }
            if let Some(v) = line.strip_prefix("application.process.id =") {
                pid = Some(v.trim().trim_matches('"').to_string());
            } else if let Some(v) = line.strip_prefix("node.name =") {
                name = Some(v.trim().trim_matches('"').to_string());
            } else if let Some(v) = line.strip_prefix("application.name =") {
                app = Some(v.trim().trim_matches('"').to_string());
            }
        }
        if index.is_none() {
            if let Some(h) = head.take() {
                if pa_sink_input_matches(&h, &pid, &name, &app, &stream_name) {
                    index = Some(h);
                }
            }
        }

        let Some(index) = index else {
            return Ok(false);
        };

        let sink_name = with_session(|core, mainloop| {
            Self::resolve_node_name(core, mainloop, sink_ref)
        })?;

        let status = Command::new("pactl")
            .arg("move-sink-input")
            .arg(&index)
            .arg(&sink_name)
            .status();
        match status {
            Ok(s) if s.success() => Ok(true),
            _ => Ok(false),
        }
    }
}

// ---------------------------------------------------------------------------
// Helper percorso PulseAudio (`pactl`)
// ---------------------------------------------------------------------------

fn is_pactl_available() -> bool {
    let p = std::env::var_os("PACTL_BIN").unwrap_or_else(|| "pactl".into());
    if std::path::Path::new(&p).is_absolute() {
        return std::fs::metadata(&p).map(|m| m.is_file()).unwrap_or(false);
    }
    let path_var = std::env::var_os("PATH").unwrap_or_default();
    for dir in std::env::split_paths(&path_var) {
        if dir.join(&p).is_file() {
            return true;
        }
    }
    false
}

/// Decide se una sink-input PA corrisponde al nostro stream PipeWire.
/// `stream` è la tupla (node.name, application.name, application.process.id).
fn pa_sink_input_matches(
    _index: &str,
    pid: &Option<String>,
    name: &Option<String>,
    app: &Option<String>,
    stream: &(String, String, String),
) -> bool {
    let (stream_node, stream_app, stream_pid) = stream;
    let mut score = 0i32;
    if !stream_pid.is_empty() && stream_pid != "0" {
        if pid.as_deref() == Some(stream_pid.as_str()) {
            score += 2;
        } else {
            score -= 1; // PID diverso → non è il nostro client
        }
    }
    if !stream_node.is_empty() && name.as_deref() == Some(stream_node.as_str()) {
        score += 1;
    }
    if !stream_app.is_empty() && app.as_deref() == Some(stream_app.as_str()) {
        score += 1;
    }
    score >= 1
}

#[cfg(test)]
mod live_tests {
    use super::*;

    fn ready() -> bool {
        std::env::var("FT_LIVE").is_err()
    }

    fn backend() -> PipeWireBackend {
        if std::env::var("XDG_RUNTIME_DIR").is_err() {
            std::env::set_var("XDG_RUNTIME_DIR", "/run/user/1000");
        }
        PipeWireBackend::new()
    }

    #[test]
    fn live_input_volume_mute_roundtrip() {
        if ready() {
            eprintln!("skip: impostare FT_LIVE=1 per i test live");
            return;
        }
        let b = backend();
        let inputs = b.list_inputs().expect("list_inputs");
        let input = inputs
            .into_iter()
            .find(|i| !i.name.ends_with(".monitor"))
            .expect("nessun input");
        let original = input.volume;
        let target = if original > 0.5 { 0.2 } else { 0.8 };
        b.set_input_volume(&input.id, target).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(120));
        let back = b
            .list_inputs()
            .unwrap()
            .into_iter()
            .find(|i| i.id == input.id)
            .expect("input sparito");
        assert!(
            (back.volume - target).abs() < 0.05,
            "vol atteso {target:.2} letto {:.2}",
            back.volume
        );

        b.set_input_mute(&input.id, true).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(120));
        let m = b.list_inputs().unwrap().into_iter().find(|i| i.id == input.id).unwrap();
        assert!(m.muted, "mute on non applicato");

        b.set_input_mute(&input.id, false).unwrap();
        b.set_input_volume(&input.id, original).unwrap();
    }

    #[test]
    fn live_link_create_destroy_roundtrip() {
        if ready() {
            eprintln!("skip: impostare FT_LIVE=1 per i test live");
            return;
        }
        let b = backend();
        let sinks = b.list_sinks().expect("list_sinks");
        let sink = sinks.first().expect("servono sink");
        let out = sink.node_id;

        with_session(|core, mainloop| {
            // Trova un nodo con porte di INPUT (Stream/Input/Audio) tra quelli
            // già raggiunti da un link (dimostra che accetta link in ingresso).
            let globals = collect_globals(core, mainloop)?;
            let mut inn = None;
            for g in &globals {
                if g.type_ != pw::types::ObjectType::Node {
                    continue;
                }
                if g.props.get("media.class") != Some(&"Stream/Input/Audio".to_string()) {
                    continue;
                }
                inn = Some(g.id);
                break;
            }
            let inn = inn.expect("servono nodi Stream/Input/Audio");

            let count = |core: &pw::core::CoreRc,
                         mainloop: &pw::main_loop::MainLoopRc|
             -> Result<Vec<u32>, BackendError> {
                let globals = collect_globals(core, mainloop)?;
                Ok(globals
                    .iter()
                    .filter(|g| g.type_ == pw::types::ObjectType::Link)
                    .filter(|g| {
                        g.props.get("link.output.node") == Some(&out.to_string())
                            && g.props.get("link.input.node") == Some(&inn.to_string())
                    })
                    .map(|g| g.id)
                    .collect())
            };

            let before = count(core, mainloop)?;

            create_link(core, mainloop, out, inn).expect("creazione link");

            // Il nuovo link deve comparire (non ci si affida allo stato precedente).
            let mut after = count(core, mainloop)?;
            assert!(
                after.len() > before.len(),
                "link creato non compare nel registry (prima {} dopo {})",
                before.len(),
                after.len()
            );
            let new_ids: Vec<u32> = after
                .iter()
                .filter(|id| !before.contains(id))
                .copied()
                .collect();
            assert!(!new_ids.is_empty(), "nessun nuovo id individuato");

            destroy_links(core, mainloop, &new_ids)?;
            after = count(core, mainloop)?;
            assert_eq!(
                after.len(),
                before.len(),
                "il link appena creato non è stato distrutto (lingered)"
            );
            Ok(())
        })
        .expect("roundtrip")
    }
}