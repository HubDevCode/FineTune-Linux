// FineTune Linux — CLI di controllo.
//
// Comandi:
//   list-sinks
//   list-streams
//   list-inputs
//   set-volume        <sink-id>       <0.0..1.0>
//   set-mute          <sink-id>       <true|false>
//   set-input-volume  <input-id>      <0.0..1.0>
//   set-input-mute    <input-id>      <true|false>
//   set-stream-volume <stream-id>     <0.0..1.0> [boost 1..4]
//   set-stream-mute   <stream-id>     <true|false>
//   set-stream-links  <stream-id>     <sink-id...>
//   set-default       <sink-id>
//   set-default-input <input-id>

use finetune_backend_pipewire::PipeWireBackend;
use finetune_core::backend::{
    AudioBackend, AudioInput, AudioSink, AudioStream, BackendError,
};

fn print_sinks(sinks: &[AudioSink]) {
    println!("{:<8} {:<6} {:<7} {:<11} {:<48} {}", "SINK ID", "CH", "VOL", "MUTE", "NAME", "DEFAULT");
    for s in sinks {
        println!(
            "{:<8} {:<6} {:<7.2} {:<11} {:<48} {}",
            s.id,
            s.channels,
            s.volume,
            s.muted,
            if s.description.is_empty() { s.name.clone() } else { s.description.clone() },
            if s.is_default { "*" } else { "" }
        );
    }
}

fn print_streams(streams: &[AudioStream]) {
    println!("{:<10} {:<8} {:<7} {:<24} {:<24} {}", "STREAM ID", "PID", "VOL", "APP", "MEDIA", "LINKS");
    for st in streams {
        println!(
            "{:<10} {:<8} {:<7.2} {:<24} {:<24} {:?}",
            st.id, st.pid, st.volume, st.app_name, st.media_name, st.linked_sink_ids
        );
    }
}

fn print_inputs(inputs: &[AudioInput]) {
    println!("{:<10} {:<7} {:<4} {:<11} {:<24} {}", "INPUT ID", "VOL", "CH", "MUTE", "NAME", "DEFAULT");
    for i in inputs {
        println!(
            "{:<10} {:<7.2} {:<4} {:<11} {:<24} {}",
            i.id,
            i.volume,
            i.channels,
            i.muted,
            if i.description.is_empty() { i.name.clone() } else { i.description.clone() },
            if i.is_default { "*" } else { "" }
        );
    }
}

fn parse_arg(args: &[String], i: usize, what: &str) -> BackendError {
    if args.get(i).is_none() {
        BackendError::Unsupported(format!("argomento mancante: {what}"))
    } else {
        BackendError::Unsupported(format!("valore non valido per {what}"))
    }
}

fn main() -> Result<(), BackendError> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        eprintln!(
            "uso: finetune <list-sinks|list-streams|set-volume|set-mute|set-stream-volume|set-stream-mute|set-default> …"
        );
        std::process::exit(2);
    }

    let backend = PipeWireBackend::new();
    let volume = |i: usize| -> Result<f32, BackendError> {
        args.get(i)
            .ok_or_else(|| parse_arg(&args, i, "volume"))
            .and_then(|v| v.parse().map_err(|_| parse_arg(&args, i, "volume")))
    };
    let flag = |i: usize| -> Result<bool, BackendError> {
        args.get(i)
            .ok_or_else(|| parse_arg(&args, i, "true|false"))
            .and_then(|v| v.parse().map_err(|_| parse_arg(&args, i, "true|false")))
    };
    let id = |i: usize| -> Result<&str, BackendError> {
        args.get(i)
            .map(String::as_str)
            .ok_or_else(|| parse_arg(&args, i, "id"))
    };

    match args[0].as_str() {
        "list-sinks" => {
            let sinks = backend.list_sinks()?;
            print_sinks(&sinks);
        }
        "list-streams" => {
            let streams = backend.list_streams()?;
            print_streams(&streams);
        }
        "list-inputs" => {
            let inputs = backend.list_inputs()?;
            print_inputs(&inputs);
        }
        "set-volume" => {
            let v = volume(2)?;
            if !(0.0..=1.0).contains(&v) {
                return Err(BackendError::Unsupported("volume fuori scala (0.0..1.0)".into()));
            }
            backend.set_sink_volume(id(1)?, v)?;
        }
        "set-mute" => {
            backend.set_sink_mute(id(1)?, flag(2)?)?;
        }
        "set-stream-volume" => {
            let v = volume(2)?;
            if !(0.0..=1.0).contains(&v) {
                return Err(BackendError::Unsupported("volume fuori scala (0.0..1.0)".into()));
            }
            let boost = args
                .get(3)
                .and_then(|b| b.parse::<f32>().ok())
                .unwrap_or(1.0);
            backend.set_stream_volume_with_boost(id(1)?, v, boost)?;
        }
        "set-stream-mute" => {
            backend.set_stream_mute(id(1)?, flag(2)?)?;
        }
        "set-stream-links" => {
            let sink_ids: Vec<String> = args[2..].to_vec();
            backend.set_stream_links(id(1)?, &sink_ids)?;
        }
        "set-input-volume" => {
            let v = volume(2)?;
            if !(0.0..=1.0).contains(&v) {
                return Err(BackendError::Unsupported("volume fuori scala (0.0..1.0)".into()));
            }
            backend.set_input_volume(id(1)?, v)?;
        }
        "set-input-mute" => {
            backend.set_input_mute(id(1)?, flag(2)?)?;
        }
        "set-default" => {
            backend.set_default_sink(id(1)?)?;
        }
        "set-default-input" => {
            backend.set_default_input(id(1)?)?;
        }
        other => {
            eprintln!("comando sconosciuto: {other}");
        }
    }
    Ok(())
}