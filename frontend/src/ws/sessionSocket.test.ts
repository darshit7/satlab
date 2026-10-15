import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

/** Just enough of a browser WebSocket to drive SessionSocket by hand. */
class FakeWebSocket {
  static readonly OPEN = 1
  static instances: FakeWebSocket[] = []
  readyState = 0
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: ((event: { code: number }) => void) | null = null
  readonly url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {}
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }
  receive(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) })
  }
}

vi.stubGlobal('WebSocket', FakeWebSocket)
vi.stubGlobal('window', { location: { protocol: 'https:', host: 'satlab.example' } })

const { SessionSocket } = await import('./sessionSocket')

const handlers = () => ({
  onMessage: vi.fn(),
  onStatus: vi.fn(),
  onOpen: vi.fn(),
  onSessionUnavailable: vi.fn(),
  onServerBusy: vi.fn(),
})
const latest = () => FakeWebSocket.instances.at(-1)!

describe('SessionSocket', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('authenticates first, without the token in the URL', () => {
    const h = handlers()
    h.onOpen.mockImplementation(() => socket.send({ type: 'set_tracked', norad_ids: [25544] }))
    const socket = new SessionSocket(7, 'secret-token', h)
    socket.connect()
    latest().open()

    expect(latest().url).toMatch(/^wss:\/\/satlab\.example\/(satlab\/)?ws\/sessions\/7$/)
    expect(latest().url).not.toContain('secret-token')
    expect(latest().sent.map((m) => JSON.parse(m).type)).toEqual(['auth', 'set_tracked'])
    expect(JSON.parse(latest().sent[0]!)).toEqual({ type: 'auth', token: 'secret-token' })
  })

  it('gives up on a session the server refuses', () => {
    for (const code of [4401, 4404]) {
      const h = handlers()
      new SessionSocket(7, 't', h).connect()
      latest().open()
      latest().onclose?.({ code })
      expect(h.onSessionUnavailable).toHaveBeenCalledOnce()
      vi.runAllTimers()
      expect(latest().onopen).not.toBeNull() // no reconnect created a new socket
    }
    expect(FakeWebSocket.instances).toHaveLength(2)
  })

  it('backs off while the server is at capacity and reports it once', () => {
    const h = handlers()
    new SessionSocket(7, 't', h).connect()
    const delays: number[] = []
    for (let i = 0; i < 3; i++) {
      latest().open()
      latest().receive({ type: 'error', message: 'mission control is at capacity; retrying' })
      latest().onclose?.({ code: 4503 })
      const before = Date.now()
      const count = FakeWebSocket.instances.length
      while (FakeWebSocket.instances.length === count) vi.advanceTimersByTime(100)
      delays.push(Date.now() - before)
    }
    expect(delays).toEqual([500, 1000, 2000])
    expect(h.onServerBusy).toHaveBeenCalledOnce()

    latest().open()
    latest().receive({ type: 'snapshot' }) // admitted: busy streak over, backoff reset
    latest().onclose?.({ code: 1006 })
    expect(h.onServerBusy).toHaveBeenCalledOnce()
    const count = FakeWebSocket.instances.length
    vi.advanceTimersByTime(500)
    expect(FakeWebSocket.instances).toHaveLength(count + 1)
  })
})
