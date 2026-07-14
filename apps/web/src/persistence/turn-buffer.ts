import { openDB, type IDBPDatabase } from "idb";

// A durable write-ahead buffer for the *current* PTT turn's outbound audio
// frames (Spec 04 §2 Phase 8 — "IndexedDB buffer replays missing chunks
// reliably upon automatic session resumption"). Scope, stated plainly: the
// gateway has no partial-turn-resume protocol (a fresh `activity_start`
// always mints a brand new server-side turn_id, see
// apps/api-gateway/app/routers/ws_exam.py), so there is no way to splice a
// dropped turn back in mid-stream. What this buffer guarantees instead is
// that a turn interrupted by a socket drop is never silently lost: every
// frame sent during an active PTT hold is persisted here first, and if the
// connection drops before that turn's `turn_complete` ack arrives, the
// reconnected client replays the whole turn from scratch (a fresh
// activity_start -> the buffered frames, in order -> activity_end).

const DB_NAME = "ielts-turn-buffer";
const DB_VERSION = 1;
const STORE = "frames";

interface BufferedFrame {
  turnId: string;
  seq: number;
  bytes: ArrayBuffer;
}

let dbPromise: Promise<IDBPDatabase> | null = null;

function getDb(): Promise<IDBPDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        const store = db.createObjectStore(STORE, { keyPath: ["turnId", "seq"] });
        store.createIndex("turnId", "turnId");
      },
    });
  }
  return dbPromise;
}

export class TurnBuffer {
  private currentTurnId: string | null = null;
  private nextSeq = 0;

  /** Call once per PTT press, before any frames are sent. */
  beginTurn(turnId: string): void {
    this.currentTurnId = turnId;
    this.nextSeq = 0;
  }

  /** Fire-and-forget from the caller's perspective — must never block or
   * delay the actual socket send, which is the latency-sensitive path. */
  async appendFrame(bytes: ArrayBuffer): Promise<void> {
    if (!this.currentTurnId) return;
    const db = await getDb();
    await db.put(STORE, { turnId: this.currentTurnId, seq: this.nextSeq++, bytes });
  }

  /** Call once the server has ack'd this turn as complete — clears the
   * buffer since there is nothing left to replay for it. */
  async completeTurn(turnId: string): Promise<void> {
    const db = await getDb();
    const tx = db.transaction(STORE, "readwrite");
    const index = tx.store.index("turnId");
    let cursor = await index.openCursor(IDBKeyRange.only(turnId));
    while (cursor) {
      await cursor.delete();
      cursor = await cursor.continue();
    }
    await tx.done;
    if (this.currentTurnId === turnId) {
      this.currentTurnId = null;
    }
  }

  /** Returns (and clears) whatever turn was left buffered with no matching
   * `completeTurn` call — i.e. the connection dropped mid-turn. Clears
   * immediately regardless of what the caller does with the result: a
   * reconnect-time replay is best-effort, and there's nothing more durable
   * than "we handed this back once" to build on top of it. */
  async takeIncompleteTurn(): Promise<{ turnId: string; frames: ArrayBuffer[] } | null> {
    const db = await getDb();
    const all: BufferedFrame[] = await db.getAll(STORE);
    if (all.length === 0) return null;

    const turnId = all[0].turnId;
    const frames = all
      .filter((f) => f.turnId === turnId)
      .sort((a, b) => a.seq - b.seq)
      .map((f) => f.bytes);

    await this.completeTurn(turnId);
    return { turnId, frames };
  }
}
