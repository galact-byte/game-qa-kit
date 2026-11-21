using System;
using System.Text.RegularExpressions;

namespace GameQaKit.Bakin
{
    public sealed class QueryEvidence
    {
        public string ActionId;
        public long SampleSequence;
        public int QueryType, GameState;
        public ulong At, Value, HoldUntil;
    }

    // Callers serialize this bounded state with their callback lock; it performs no I/O.
    public sealed class InputState
    {
        static readonly Regex Identifier = new Regex(@"\A[A-Za-z0-9_-]{1,64}\z");
        readonly ulong[] consumedTriggers = new ulong[3];
        ulong desired, current, trigger, released, repeat, leaseUntil, holdUntil, startDeadline, duration, pressedAt, sampledAt;
        long lastSequence;
        bool permitted;
        public long SampleSequence { get; private set; }
        public string ActionId { get; private set; }
        public ulong ActionKey { get; private set; }
        public string Error { get; private set; }
        public QueryEvidence PressEvidence { get; private set; }
        public QueryEvidence ReleaseEvidence { get; private set; }
        public ulong Held { get { return current & desired; } }

        public void Grant(ulong deadline, ulong now)
        {
            if (Error != null) throw new InvalidOperationException(Error);
            if (deadline <= now || deadline - now > 1500) throw new ArgumentException("Invalid lease deadline");
            Expire(now);
            permitted = true;
            leaseUntil = deadline;
        }

        public bool Begin(long sequence, string actionId, ulong key, ulong holdDuration, ulong deadline, ulong now)
        {
            if (Error != null) throw new InvalidOperationException(Error);
            if (sequence <= 0 || !Identifier.IsMatch(actionId ?? "") || !Supported(key)
                || holdDuration == 0 || holdDuration > 1000 || deadline > (ulong)Int64.MaxValue - holdDuration
                || deadline <= now || deadline - now > 1000)
                throw new ArgumentException("Invalid action, key or deadline");
            if (sequence <= lastSequence) return false;
            Expire(now);
            if (!permitted) throw new InvalidOperationException("Input permission is absent");
            if (desired != 0 || (ActionId != null && PressEvidence != null && ReleaseEvidence == null))
                throw new InvalidOperationException("Previous action has not released");
            lastSequence = sequence;
            ActionId = actionId;
            ActionKey = desired = key;
            startDeadline = deadline;
            duration = holdDuration;
            holdUntil = 0;
            PressEvidence = ReleaseEvidence = null;
            return true;
        }

        public void Sample(ulong now)
        {
            if (SampleSequence == Int64.MaxValue) { Fault("sample_sequence_overflow"); return; }
            if (SampleSequence != 0 && now < sampledAt) { Fault("clock_regression"); return; }
            Expire(now);
            ulong previous = current;
            current = desired;
            trigger = current & ~previous;
            released = previous & ~current;
            if (trigger != 0) pressedAt = now;
            repeat = trigger;
            if (current != 0 && now - pressedAt > 500
                && (now - pressedAt) * 60 / 4000 > (sampledAt - pressedAt) * 60 / 4000)
                repeat |= current;
            Array.Clear(consumedTriggers, 0, consumedTriggers.Length);
            sampledAt = now;
            SampleSequence++;
        }

        public ulong Query(int type, int context, ulong now)
        {
            Expire(now);
            if (context < 0 || context >= 3) return 0;
            ulong value = 0;
            switch (type)
            {
                case 0: value = current & desired; break;
                case 1: value = trigger & desired & ~consumedTriggers[context]; break;
                case 2: value = released; break;
                case 3: value = repeat & desired; break;
            }
            Record(type, context, now, value, 0);
            return value;
        }

        public float Axis(ulong key, int context, ulong now)
        {
            Expire(now);
            if (key > 8 || !Supported(key) || context < 0 || context >= 3) return 0;
            ulong value = current & desired & key;
            Record(4, context, now, value, key);
            return value == 0 ? 0 : 1;
        }

        public void ConsumeTrigger(ulong key, int context)
        {
            if (context >= 0 && context < 3) consumedTriggers[context] |= key;
        }

        public void Pause()
        {
            permitted = false;
            desired = holdUntil = startDeadline = duration = 0;
        }

        public void Fault(string reason)
        {
            if (Error == null) Error = reason;
            Pause();
        }

        void Expire(ulong now)
        {
            if (Error != null || !permitted || now >= leaseUntil) Pause();
            else if (now >= (PressEvidence == null ? startDeadline : holdUntil)) desired = 0;
        }

        void Record(int type, int context, ulong now, ulong value, ulong axisKey)
        {
            if (ActionId == null || SampleSequence == 0 || (type == 4 && axisKey != ActionKey)) return;
            if (PressEvidence == null && type != 2 && (value & ActionKey) != 0)
            {
                holdUntil = now + duration;
                PressEvidence = Evidence(type, context, now, value);
            }
            // Repeat gaps occur while held; require a neutral sample in the consuming context.
            if (ReleaseEvidence == null && PressEvidence != null && desired == 0
                && (type == 0 || type == 4 || type == 3 && context == PressEvidence.GameState && (current & ActionKey) == 0)
                && (value & ActionKey) == 0)
                ReleaseEvidence = Evidence(type, context, now, value);
        }

        QueryEvidence Evidence(int type, int context, ulong now, ulong value)
        {
            return new QueryEvidence { ActionId = ActionId, SampleSequence = SampleSequence,
                QueryType = type, GameState = context, At = now, Value = value, HoldUntil = holdUntil };
        }

        public static bool Supported(ulong key)
        {
            return key == 1 || key == 2 || key == 4 || key == 8 || key == 16 || key == 32;
        }
    }
}
