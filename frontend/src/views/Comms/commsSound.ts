// Short sound cues for command traffic: a rising chirp when a command goes up, a two-note chime
// when its answer comes back, a low bonk when the answer is lost. Web Audio oscillators only: no
// files, no worklets. One engine per page, created by the sound toggle's click (browsers start
// audio only after a user gesture) and kept for the page's life.

import type { Cue } from '../../lib/audio'

const MASTER_LEVEL = 0.3
const LEVEL = 0.35

interface Note {
  /** Seconds after the cue starts. */
  at: number
  duration: number
  from: number
  to: number
  type: OscillatorType
}

const CUES: Record<Cue, Note[]> = {
  sent: [{ at: 0, duration: 0.11, from: 660, to: 990, type: 'sine' }],
  reply: [
    { at: 0, duration: 0.08, from: 880, to: 880, type: 'sine' },
    { at: 0.09, duration: 0.14, from: 1320, to: 1320, type: 'sine' },
  ],
  'no-reply': [{ at: 0, duration: 0.18, from: 220, to: 150, type: 'triangle' }],
}

export class CommsSound {
  private readonly ctx = new AudioContext()
  private readonly out: AudioNode

  constructor() {
    const master = this.ctx.createGain()
    master.gain.value = MASTER_LEVEL
    master.connect(this.ctx.destination)
    // soft edges: nothing shrill
    const lowpass = this.ctx.createBiquadFilter()
    lowpass.type = 'lowpass'
    lowpass.frequency.value = 3000
    lowpass.connect(master)
    this.out = lowpass
  }

  resume(): Promise<void> {
    return this.ctx.resume()
  }

  /** Play `cue`, `delayS` from now (when its packet's dot arrives). */
  play(cue: Cue, delayS: number): void {
    const start = this.ctx.currentTime + Math.max(0, delayS)
    for (const note of CUES[cue]) {
      const t0 = start + note.at
      const t1 = t0 + note.duration
      const osc = this.ctx.createOscillator()
      osc.type = note.type
      osc.frequency.setValueAtTime(note.from, t0)
      if (note.to !== note.from) osc.frequency.exponentialRampToValueAtTime(note.to, t1)
      const gain = this.ctx.createGain()
      gain.gain.setValueAtTime(0, t0)
      gain.gain.linearRampToValueAtTime(LEVEL, t0 + 0.008)
      gain.gain.exponentialRampToValueAtTime(0.001, t1)
      osc.connect(gain)
      gain.connect(this.out)
      osc.start(t0)
      osc.stop(t1 + 0.02)
    }
  }
}

let engine: CommsSound | null = null

/** Create (or wake) the page's engine: call it from the click that turns the sound on. Null when
 *  the browser has no Web Audio. */
export function startCommsSound(): CommsSound | null {
  if (typeof AudioContext === 'undefined') return null
  engine ??= new CommsSound()
  void engine.resume()
  return engine
}

/** The engine, if a click has started it. */
export function commsSound(): CommsSound | null {
  return engine
}
