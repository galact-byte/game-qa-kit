using System;
using GameQaKit.Bakin;

static class StateTests
{
    static void Equal<T>(T expected, T actual, string message)
    {
        if (!Object.Equals(expected, actual)) throw new Exception(message + ": expected " + expected + ", got " + actual);
    }
    static InputState Start(ulong key, ulong now, ulong duration)
    {
        var state = new InputState();
        state.Grant(now + 1500, now);
        state.Begin(1, "action-1", key, duration, now + 1000, now);
        state.Sample(now);
        return state;
    }
    static void Main(string[] args)
    {
        switch (args[0])
        {
            case "consume-start":
            {
                var s = Start(16, 1000, 100);
                Equal(16UL, s.Query(0, 0, 1020), "first ordinary query consumes press");
                s.Sample(1100);
                Equal(16UL, s.Query(0, 0, 1100), "full duration starts at consumption, not dispatch");
                s.Sample(1120);
                Equal(0UL, s.Query(0, 0, 1120), "fixed deadline expires without extension");
                Equal(1020UL, s.PressEvidence.At, "first query is the timing anchor");
                Equal(true, s.ReleaseEvidence != null, "ordinary neutral query confirms release");
                break;
            }
            case "delayed-sample":
            {
                var s = new InputState(); s.Grant(2500, 1000);
                s.Begin(1, "late", 16, 100, 2000, 1000);
                s.Sample(1125); Equal(true, s.PressEvidence == null, "sample alone is not consumption");
                Equal(16UL, s.Query(0, 0, 1125), "125ms delay still consumes a valid press");
                Equal(1225UL, s.PressEvidence.HoldUntil, "fixed deadline is exactly 100ms after first query");
                s.Grant(2700, 1200); s.Sample(1224);
                Equal(16UL, s.Query(0, 0, 1224), "held through duration minus one");
                s.Sample(1225); Equal(0UL, s.Query(0, 0, 1225), "queries and renewal never extend fixed deadline");
                break;
            }
            case "start-expiry":
            {
                var s = Start(16, 1000, 100);
                Equal(true, s.PressEvidence == null, "unconsumed sample");
                s.Grant(3499, 1999); s.Sample(2000);
                Equal(0UL, s.Query(0, 0, 2000), "start boundary cancels unconsumed press");
                s.Grant(3600, 2100); s.Sample(2100);
                Equal(0UL, s.Query(0, 0, 2100), "renewal cannot revive expired pending action");
                Equal(true, s.PressEvidence == null, "expired action has no success evidence");
                break;
            }
            case "pending-cancel":
            {
                foreach (string reason in new[]{"pause", "lease", "fault"})
                {
                    var s = Start(16, 1000, 100);
                    if(reason == "pause") s.Pause();
                    else if(reason == "fault") s.Fault("io_failed");
                    else s.Query(0, 0, 2500);
                    if(reason != "fault") s.Grant(4100, 2600);
                    s.Sample(2600); Equal(0UL, s.Query(0, 0, 2600), reason + " cancels pending action");
                    Equal(true, s.PressEvidence == null, "cancelled pending press has no consumption evidence");
                }
                break;
            }
            case "timing-bounds":
            {
                foreach(ulong d in new[]{0UL, 1001UL, UInt64.MaxValue})
                {
                    var s = new InputState(); s.Grant(2500,1000);
                    bool rejected=false;
                    try { s.Begin(1,"a",16,d,2000,1000); } catch(ArgumentException) { rejected=true; }
                    Equal(true,rejected,"invalid duration rejected");
                }
                break;
            }
            case "edges":
            {
                var s = Start(16, 1000, 1000);
                Equal(16UL, s.Query(0, 0, 1000), "held");
                Equal(16UL, s.Query(1, 0, 1000), "press edge");
                Equal(16UL, s.Query(1, 0, 1000), "repeated same-generation query");
                Equal(0UL, s.Query(2, 0, 1000), "no release on press");
                s.Sample(1020);
                Equal(16UL, s.Query(0, 0, 1020), "held in next sample");
                Equal(0UL, s.Query(1, 0, 1020), "no second press");
                s.Sample(2000);
                Equal(0UL, s.Query(0, 0, 2000), "deadline clears held");
                Equal(16UL, s.Query(2, 0, 2000), "release edge");
                s.Sample(2020);
                Equal(0UL, s.Query(2, 0, 2020), "release edge lasts one sample");
                break;
            }
            case "repeat":
            {
                var s = Start(2, 1000, 1000);
                Equal(2UL, s.Query(3, 1, 1000), "initial repeat");
                s.Sample(1200); Equal(0UL, s.Query(3, 1, 1200), "initial repeat delay");
                s.Sample(1500); Equal(0UL, s.Query(3, 1, 1500), "strict 30-tick delay");
                s.Sample(1534); Equal(2UL, s.Query(3, 1, 1534), "four-tick repeat interval");
                s.Sample(1550); Equal(0UL, s.Query(3, 1, 1550), "repeat is a pulse");
                break;
            }
            case "consume":
            {
                var s = Start(16, 1000, 100);
                Equal(true, s.PressEvidence == null, "sampling is not consumption");
                s.Query(1, 1, 1020);
                Equal("action-1", s.PressEvidence.ActionId, "ordinary query associates action");
                Equal(1L, s.PressEvidence.SampleSequence, "query sample identity");
                s.Sample(1120);
                Equal(true, s.ReleaseEvidence == null, "clearing state is not release consumption");
                s.Query(1, 1, 1120);
                Equal(true, s.ReleaseEvidence == null, "neutral trigger is not held-state evidence");
                s.Query(0, 1, 1120);
                Equal("action-1", s.ReleaseEvidence.ActionId, "ordinary neutral query associates release");
                break;
            }
            case "repeat-consume":
            {
                var s = Start(2, 1000, 100);
                Equal(2UL, s.Query(3, 1, 1000), "title consumes direction through repeat");
                s.Sample(1050);
                Equal(0UL, s.Query(3, 1, 1050), "repeat gap while held");
                Equal(true, s.ReleaseEvidence == null, "repeat gap is not release");
                s.Sample(1100);
                s.Query(3, 0, 1100);
                Equal(true, s.ReleaseEvidence == null, "unrelated context cannot confirm repeat release");
                s.Query(3, 1, 1100);
                Equal(true, s.ReleaseEvidence != null, "ordinary neutral title query confirms release");
                Equal("action-1", s.ReleaseEvidence.ActionId, "repeat release action identity");
                Equal(3, s.ReleaseEvidence.QueryType, "release records actual repeat query");
                Equal(3L, s.ReleaseEvidence.SampleSequence, "release follows neutral input sample");
                break;
            }
            case "trigger-repeat-release":
            {
                var s = Start(16, 1000, 100);
                s.Query(1, 1, 1000);
                s.Sample(1100);
                s.Query(3, 1, 1100);
                Equal(true, s.ReleaseEvidence != null, "neutral ordinary menu query confirms released confirm key");
                Equal(0UL, s.ReleaseEvidence.Value, "query returned neutral keys");
                break;
            }
            case "pause":
            {
                var s = Start(16, 1000, 1000);
                s.Pause();
                Equal(0UL, s.Query(0, 0, 1001), "pause neutral before next sample");
                s.Sample(1002); Equal(16UL, s.Query(2, 0, 1002), "pause release edge");
                s.Grant(2500, 1100); s.Sample(1100);
                Equal(0UL, s.Query(0, 0, 1100), "resume never replays old action");
                break;
            }
            case "lease":
            {
                var s = new InputState(); s.Grant(1100, 1000); s.Begin(1, "a", 1, 1000, 2000, 1000); s.Sample(1000);
                Equal(1UL, s.Query(0, 0, 1000), "lease initially permits held input");
                Equal(0UL, s.Query(0, 0, 1100), "lease expires even between samples");
                s.Grant(2600, 1100); s.Sample(1100);
                Equal(0UL, s.Query(0, 0, 1100), "expired lease cannot replay action");
                break;
            }
            case "renewal":
            {
                var s = Start(4, 1000, 100);
                Equal(4UL, s.Query(0, 0, 1000), "action initially held");
                s.Grant(2590, 1090); s.Sample(1100);
                Equal(0UL, s.Query(0, 0, 1100), "renewal cannot extend hold deadline");
                break;
            }
            case "suspend":
            {
                var s = Start(8, 1000, 1000);
                Equal(8UL, s.Query(0, 0, 1000), "input before suspension");
                Equal(0UL, s.Query(0, 0, 9000), "first query after suspension is neutral");
                Equal(0UL, s.Query(1, 0, 9000), "no stale press after suspension");
                break;
            }
            case "axis":
            {
                var s = Start(4, 1000, 100);
                Equal(1f, s.Axis(4, 0, 1000), "held direction magnitude");
                Equal(0f, s.Axis(8, 0, 1000), "other direction neutral");
                Equal(0f, s.Axis(4, 0, 1100), "axis honors hold deadline");
                Equal(0f, s.Axis(128, 0, 1000), "unsupported axis neutral");
                break;
            }
            case "trigger-consumed":
            {
                var s = Start(16, 1000, 100);
                s.ConsumeTrigger(16, 1);
                Equal(0UL, s.Query(1, 1, 1000), "consumed trigger cleared for its context");
                Equal(16UL, s.Query(1, 0, 1000), "other context preserves trigger");
                break;
            }
            case "duplicate":
            {
                var s = Start(1, 1000, 100);
                s.Query(0, 0, 1000);
                Equal(false, s.Begin(1, "action-1", 1, 100, 2000, 1050), "duplicate rejected");
                s.Sample(1100); Equal(0UL, s.Query(0, 0, 1100), "duplicate cannot rearm");
                break;
            }
            case "fault":
            {
                var s = Start(16, 1000, 1000); s.Fault("channel_corrupt");
                Equal(0UL, s.Query(0, 0, 1001), "fault neutral immediately");
                bool rejected = false; try { s.Grant(2600, 1100); } catch (InvalidOperationException) { rejected = true; }
                Equal(true, rejected, "fault latches session closed");
                break;
            }
            case "illegal":
            {
                var s = new InputState(); s.Grant(2500, 1000);
                int rejected = 0;
                foreach (ulong key in new ulong[] { 0, 3, 64, 128, UInt64.MaxValue })
                    try { s.Begin(1, "a", key, 100, 2000, 1000); } catch (ArgumentException) { rejected++; }
                Equal(5, rejected, "unsupported keys and combinations rejected");
                break;
            }
            default: throw new Exception("Unknown test case");
        }
        Console.WriteLine("PASS " + args[0]);
    }
}
