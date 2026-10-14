import { WS_CLOSE_SESSION_UNAVAILABLE, type ClientMessage, type ServerMessage } from '../types/ws'
import { appUrl } from '../lib/basePath'

export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface SessionSocketHandlers {
  onMessage: (message: ServerMessage) => void
  onStatus: (status: ConnectionStatus) => void
  /** Called on every (re)connect — resend any client state the server should know. */
  onOpen: () => void
  /** The server no longer has this session (ended or unknown); don't retry it. */
  onSessionUnavailable: () => void
}

const MIN_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 10_000

/** A WebSocket to one mission session with exponential-backoff reconnects. */
export class SessionSocket {
  private ws: WebSocket | null = null
  private backoffMs = MIN_BACKOFF_MS
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private closedByClient = false
  private readonly sessionId: number
  private readonly handlers: SessionSocketHandlers

  constructor(sessionId: number, handlers: SessionSocketHandlers) {
    this.sessionId = sessionId
    this.handlers = handlers
  }

  connect(): void {
    this.closedByClient = false
    this.open('connecting')
  }

  send(message: ClientMessage): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false
    this.ws.send(JSON.stringify(message))
    return true
  }

  close(): void {
    this.closedByClient = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.ws?.close()
    this.ws = null
    this.handlers.onStatus('closed')
  }

  private open(status: ConnectionStatus): void {
    this.handlers.onStatus(status)
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${window.location.host}${appUrl(`/ws/sessions/${this.sessionId}`)}`)
    this.ws = ws

    ws.onopen = () => {
      this.backoffMs = MIN_BACKOFF_MS
      this.handlers.onStatus('open')
      this.handlers.onOpen()
    }
    ws.onmessage = (event: MessageEvent<string>) => {
      this.handlers.onMessage(JSON.parse(event.data) as ServerMessage)
    }
    ws.onclose = (event) => {
      if (this.ws !== ws || this.closedByClient) return
      this.ws = null
      if (event.code === WS_CLOSE_SESSION_UNAVAILABLE) {
        this.handlers.onStatus('closed')
        this.handlers.onSessionUnavailable()
        return
      }
      this.handlers.onStatus('reconnecting')
      this.retryTimer = setTimeout(() => this.open('reconnecting'), this.backoffMs)
      this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS)
    }
  }
}
