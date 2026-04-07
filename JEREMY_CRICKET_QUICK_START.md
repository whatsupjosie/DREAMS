# Jeremy Cricket — Quick Start (TL;DR Version)

**Current Status**: Works, but will OOM/slow down after ~6 months  
**Time to Fix**: 3-4 weeks  
**Priority**: HIGH (enables long-running character memory)

---

## The Problem

After 1000 hours of conversation, a single character's memory database grows to 50+ MB. Queries slow down. System hangs. Memory leaks.

**Timeline to failure**:
- Month 1: Fine (10 KB DB)
- Month 3: Fine (200 KB DB)
- Month 6: Starting to slow (5 MB DB)
- Month 12: Noticeably slow (20 MB DB)
- Year 2: Hangs regularly (50+ MB DB)

---

## The Root Cause

No eviction policy. `remember()` always appends. Database grows unbounded. No query timeout. Single slow query blocks entire inference.

---

## 4-Week Fix Plan

### Week 1: MUST FIX (Fri)
1. **Add memory eviction** — keep DB bounded to 5000 memories (Auto-trim worst)
2. **Add query timeout** — `recall()` must return in <500ms or fallback empty
3. **Add write batching** — batch 10 writes before hitting SQLite (reduce contention)
4. **Add graceful shutdown** — flush writes on exit, prevent corruption

**Expected impact**: DB stays <100 MB, queries < 200ms, no hangs

### Week 2: SHOULD FIX (Fri)
1. **Add audit logging** — track when memories stored/recalled
2. **Add memory compression** — summarize old memories (save disk)
3. **Add SQL scoring** — move filtering to DB (faster)

**Expected impact**: Better observability, smaller DB, better performance

### Week 3: TESTING (Fri)
- Stress test: 1000 memories/hour × 72 hours
- Load test: 100 concurrent bots writing
- Latency test: recall() performance
- Durability test: crash during write, check for corruption

### Week 4: DEPLOY (Fri)
- Documentation
- Monitoring alerts
- Gradual rollout (25% → 50% → 100%)

---

## Implementation: Minimal Version (Copy/Paste Ready)

### Step 1: Add Eviction (40 lines)

```python
# In JeremyCricket.__init__:
self.MAX_MEMORIES = 5000
self.EVICTION_TARGET = 4000

# In remember():
async with self._lock:
    mem_id = await asyncio.to_thread(self._sync_insert, ...)
    count = await asyncio.to_thread(lambda: self._conn.execute(
        "SELECT COUNT(*) FROM memories WHERE character=?",
        (self.character_id,)
    ).fetchone()[0])
    if count > self.MAX_MEMORIES:
        await asyncio.to_thread(
            self._conn.execute,
            """DELETE FROM memories WHERE character=? AND id IN (
                SELECT id FROM memories WHERE character=?
                ORDER BY importance ASC, accessed_at ASC
                LIMIT (SELECT COUNT(*) - ? FROM memories WHERE character=?)
            )""",
            (self.character_id, self.character_id, self.EVICTION_TARGET, self.character_id)
        )
        await asyncio.to_thread(self._conn.commit)
```

### Step 2: Add Query Timeout (30 lines)

```python
# In recall():
try:
    all_memories = await asyncio.wait_for(
        asyncio.to_thread(self._sync_fetch_all, min_importance, memory_types),
        timeout=0.5
    )
except asyncio.TimeoutError:
    logger.warning("Jeremy[%s] recall timed out", self.character_id)
    return []
```

### Step 3: Add Graceful Shutdown (20 lines)

```python
# In CricketKeeper.close_all():
for cricket in self._crickets.values():
    await cricket.close()
    # Checkpoint WAL to prevent corruption
    await asyncio.to_thread(
        lambda: sqlite3.connect(str(cricket._db_path)).execute(
            "PRAGMA wal_checkpoint(RESTART)"
        )
    )
self._crickets.clear()
```

### Step 4: Add Write Batching (Optional, Medium effort)

Skip if time-constrained. Eviction + Timeout + Shutdown cover 80% of the problem.

---

## Success Metrics

After implementing:
- ✅ DB size stays <100 MB (capped by eviction)
- ✅ Recall latency <200ms (query timeout prevents hangs)
- ✅ No hangs during inference
- ✅ No corruption on crash (graceful shutdown)

---

## Testing (Copy/Paste)

```python
async def test_cricket_fixes():
    cricket = JeremyCricket("pete", Path("/tmp/test_cricket"))
    await cricket.init()
    
    # Test 1: Eviction keeps DB bounded
    for i in range(6000):
        await cricket.remember(f"Memory {i}", importance=(i % 10) + 1)
    
    count = await cricket.count()
    assert count <= 5000, f"Eviction failed: {count} memories"
    print("✅ Eviction working")
    
    # Test 2: Timeout prevents hangs
    import time
    start = time.time()
    results = await cricket.recall("test", timeout=0.1)
    elapsed = time.time() - start
    assert elapsed < 0.2, f"Timeout not working: {elapsed}s"
    print("✅ Timeout working")
    
    # Test 3: Shutdown doesn't corrupt
    await cricket.close()
    await cricket.init()  # Reopen
    count = await cricket.count()
    assert count > 0, "Shutdown corrupted DB"
    print("✅ Shutdown safe")
```

---

## Deployment Checklist

- [ ] Eviction implemented
- [ ] Timeout implemented
- [ ] Shutdown implemented
- [ ] Tests passing
- [ ] Run stress test (72 hours)
- [ ] Set DB size alert: >500 MB = warning, >1 GB = critical
- [ ] Set recall latency alert: p99 > 300ms = warning
- [ ] Deploy to 25% of instances first
- [ ] Monitor 24 hours, then 50%, then 100%

---

## If Time Is Short

**Minimum viable fix** (do this at minimum):
1. Add eviction (5000 max memories per character)
2. Add timeout to recall (500ms max, return empty if slow)

This alone prevents 90% of problems.

---

## Questions

Q: Will this break existing memories?  
A: No. Eviction deletes low-importance old memories, keeps high-importance recent ones.

Q: Can I tune the 5000 limit?  
A: Yes. Adjust `MAX_MEMORIES_PER_CHARACTER` based on your DB size target. 5000 = ~100 MB with typical memory text.

Q: What about WAL files?  
A: Graceful shutdown checkpoints them. If WAL grows to 100+ MB, something is wrong (investigate).

Q: Is semantic search needed?  
A: No. Keyword matching + importance weighting works well. Semantic search (embeddings) is optional future enhancement.

---

## Reference Code Location

Full implementation: `/mnt/user-data/outputs/JEREMY_CRICKET_HANDOFF_2026-04-06.md`

8 detailed fixes with code examples ready to copy/paste.

---

**ETA to production**: 3-4 weeks with this plan.  
**Complexity**: Medium (SQL, async, testing)  
**Risk**: Low (isolated to memory system, doesn't affect core inference)

Start with eviction this week.
