//! Records the default microphone into 24 kHz mono 16-bit PCM, the format sent
//! to OpenAI.

use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, SampleFormat, SizedSample, StreamConfig};

pub const RATE: u32 = 24_000;

/// The length of 24 kHz mono samples, in seconds.
pub fn seconds(samples: &[i16]) -> f64 {
    samples.len() as f64 / f64::from(RATE)
}

/// The samples as a WAV file.
pub fn wav(samples: &[i16]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut out = Vec::with_capacity(44 + samples.len() * 2);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + data_len).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes()); // PCM
    out.extend_from_slice(&1u16.to_le_bytes()); // mono
    out.extend_from_slice(&RATE.to_le_bytes());
    out.extend_from_slice(&(RATE * 2).to_le_bytes());
    out.extend_from_slice(&2u16.to_le_bytes());
    out.extend_from_slice(&16u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&data_len.to_le_bytes());
    for sample in samples {
        out.extend_from_slice(&sample.to_le_bytes());
    }
    out
}

struct Buffer {
    /// Mono samples at the device's rate.
    samples: Vec<f32>,
    max: usize,
}

/// A recording in progress; `stop` ends it.
pub struct Recording {
    stream: cpal::Stream,
    buffer: Arc<Mutex<Buffer>>,
    rate: u32,
}

impl Recording {
    /// Starts recording the default input, keeping at most `max_seconds`.
    pub fn start(max_seconds: u32) -> Result<Self> {
        let device = cpal::default_host().default_input_device().context("No microphone found")?;
        // 24 kHz mono is what OpenAI gets; PipeWire and PulseAudio convert to it. A device
        // that can't is recorded as it is and converted afterwards.
        let wanted = StreamConfig { channels: 1, sample_rate: RATE, buffer_size: cpal::BufferSize::Default };
        if let Ok(recording) = Self::build::<i16>(&device, wanted, max_seconds) {
            return Ok(recording);
        }
        let supported = device.default_input_config().context("The microphone has no usable format")?;
        let config = supported.config();
        match supported.sample_format() {
            SampleFormat::I16 => Self::build::<i16>(&device, config, max_seconds),
            SampleFormat::I32 => Self::build::<i32>(&device, config, max_seconds),
            SampleFormat::U16 => Self::build::<u16>(&device, config, max_seconds),
            SampleFormat::F64 => Self::build::<f64>(&device, config, max_seconds),
            _ => Self::build::<f32>(&device, config, max_seconds),
        }
    }

    fn build<T>(device: &cpal::Device, config: StreamConfig, max_seconds: u32) -> Result<Self>
    where
        T: SizedSample + Send + 'static,
        f32: FromSample<T>,
    {
        let rate = config.sample_rate;
        let channels = usize::from(config.channels.max(1));
        let buffer = Arc::new(Mutex::new(Buffer { samples: Vec::new(), max: (max_seconds * rate) as usize }));
        let sink = buffer.clone();
        let stream = device.build_input_stream::<T, _, _>(
            config,
            move |data: &[T], _| {
                let mut buffer = sink.lock().unwrap();
                for frame in data.chunks(channels) {
                    if buffer.samples.len() >= buffer.max {
                        return;
                    }
                    let sum: f32 = frame.iter().map(|s| s.to_sample::<f32>()).sum();
                    buffer.samples.push(sum / frame.len() as f32);
                }
            },
            |err| eprintln!("Microphone: {err}"),
            None,
        )?;
        stream.play()?;
        Ok(Self { stream, buffer, rate })
    }

    /// Ends the recording and returns it as 24 kHz mono samples.
    pub fn stop(self) -> Vec<i16> {
        drop(self.stream);
        let samples = std::mem::take(&mut self.buffer.lock().unwrap().samples);
        resample(&samples, self.rate).into_iter().map(|s| (s.clamp(-1.0, 1.0) * f32::from(i16::MAX)) as i16).collect()
    }
}

/// Linear resampling to 24 kHz: plenty for speech recognition.
fn resample(samples: &[f32], rate: u32) -> Vec<f32> {
    if rate == RATE || samples.is_empty() {
        return samples.to_vec();
    }
    let step = f64::from(rate) / f64::from(RATE);
    let len = (samples.len() as f64 / step) as usize;
    (0..len)
        .map(|i| {
            let position = i as f64 * step;
            let index = position as usize;
            let next = samples.get(index + 1).copied().unwrap_or(samples[index]);
            let fraction = (position - index as f64) as f32;
            samples[index] + (next - samples[index]) * fraction
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn writes_wav() {
        let wav = wav(&[0, 1, -1]);
        assert_eq!(&wav[..4], b"RIFF");
        assert_eq!(wav.len(), 44 + 6);
        assert_eq!(u32::from_le_bytes(wav[40..44].try_into().unwrap()), 6);
    }

    #[test]
    fn resamples() {
        assert_eq!(resample(&vec![0.0; 48_000], 48_000).len(), 24_000);
        assert_eq!(resample(&[0.5; 100], RATE).len(), 100);
    }
}
