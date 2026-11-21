using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace GameQaKit.Bakin
{
    // All methods run under the caller's short snapshot lock. No filesystem operations.
    public sealed class ChannelState
    {
        public readonly InputState Input = new InputState();
        public readonly string Session, Creation;
        public readonly int Pid;
        readonly HashSet<string> identifiers = new HashSet<string>(StringComparer.Ordinal);
        public bool Paused { get; private set; }
        public bool Closing { get; private set; }
        public long Sequence { get; private set; }
        public string Kind { get; private set; }
        public string Error { get; private set; }
        ulong leaseUntil;
        public ChannelState(string session, int pid, string creation)
        {
            if (!Identifier(session) || pid <= 0 || !Regex.IsMatch(creation ?? "", @"\A[0-9]{1,20}\z"))
                throw new ArgumentException("Invalid binding identity");
            Session = session; Pid = pid; Creation = creation; Paused = true;
        }
        public static bool Identifier(string value) { return Regex.IsMatch(value ?? "", @"\A[A-Za-z0-9_-]{1,64}\z"); }
        public static ulong Integer(object value)
        {
            if (value is int && (int)value >= 0) return (ulong)(int)value;
            if (value is long && (long)value >= 0) return (ulong)(long)value;
            // JavaScriptSerializer emits large integral JSON numbers as Decimal.
            if (value is decimal && (decimal)value >= 0 && (decimal)value <= Int64.MaxValue
                && Decimal.Truncate((decimal)value) == (decimal)value) return (ulong)(decimal)value;
            throw new ArgumentException("Expected a bounded nonnegative JSON integer");
        }
        public void Identity(Dictionary<string,object> message, params string[] fields)
        {
            string[] keys = new[]{"protocol","bridge_version","session_id","pid","creation_id"}.Concat(fields).ToArray();
            if (message == null || message.Count != keys.Length || keys.Any(k => !message.ContainsKey(k))
                || Integer(message["protocol"]) != 2 || Integer(message["bridge_version"]) != 3
                || !(message["session_id"] is string) || (string)message["session_id"] != Session
                || Integer(message["pid"]) != (ulong)Pid || !(message["creation_id"] is string)
                || (string)message["creation_id"] != Creation)
                throw new ArgumentException("Invalid message schema or identity");
        }
        public void Lease(Dictionary<string,object> message, ulong now)
        {
            Identity(message,"expires_at_ms");
            ulong expiry = Integer(message["expires_at_ms"]);
            if (expiry <= now || expiry - now > 1500) throw new ArgumentException("Invalid lease expiry");
            Tick(now);
            leaseUntil = expiry;
            if (!Paused && Error == null) Input.Grant(expiry, now);
        }
        public bool Apply(Dictionary<string,object> message, ulong now)
        {
            try
            {
                Identity(message,"sequence","id","kind","key","expires_at_ms","duration_ms","start_deadline_ms","hard_stop_at_ms");
                ulong sequence = Integer(message["sequence"]), expiry = Integer(message["expires_at_ms"]),
                    duration = Integer(message["duration_ms"]), start = Integer(message["start_deadline_ms"]),
                    stop = Integer(message["hard_stop_at_ms"]), key = Integer(message["key"]);
                var id = message["id"] as string; var kind = message["kind"] as string;
                if (!Identifier(id) || sequence == 0 || expiry <= now || expiry - now > 1500
                    || !(kind == "down" || kind == "up" || kind == "pause" || kind == "resume" || kind == "close")
                    || (kind == "down" ? !InputState.Supported(key) || duration == 0 || duration > 1000
                        || start != expiry || start - now > 1000 || start > (ulong)Int64.MaxValue - duration || stop != start + duration
                        : key != 0 || duration != 0 || start != 0 || stop != 0))
                    throw new ArgumentException("Invalid command values or deadline");
                if (sequence <= (ulong)Sequence) return false;
                if (sequence != (ulong)Sequence + 1 || identifiers.Contains(id) || identifiers.Count >= 1024 || Error != null)
                    throw new ArgumentException("Command sequence, duplicate ID, capacity or fault latch violation");
                Tick(now);
                if (kind == "down")
                {
                    if (Paused) throw new ArgumentException("Input permission is absent");
                    Input.Begin((long)sequence,id,key,duration,start,now);
                }
                else if (kind == "resume")
                {
                    if (leaseUntil <= now || leaseUntil - now > 1500) throw new ArgumentException("Resume needs a fresh lease");
                    Input.Grant(leaseUntil,now); Paused = false;
                }
                else
                {
                    Input.Pause();
                    // An explicit up ends the action; continuing permission needs a fresh grant.
                    if (kind == "up" && !Paused && leaseUntil > now) Input.Grant(leaseUntil,now);
                    else Paused = true;
                    if (kind == "close") Closing = true;
                }
                identifiers.Add(id); Sequence = (long)sequence; Kind = kind;
                return true;
            }
            catch (ArgumentException) { Fault("invalid_command"); throw; }
            catch (InvalidOperationException e) { Fault("invalid_input_state"); throw new ArgumentException(e.Message,e); }
        }
        public void Tick(ulong now)
        {
            if (leaseUntil <= now || Error != null) { Paused = true; Input.Pause(); }
        }
        public void Fault(string reason)
        {
            if (Error == null) Error = reason;
            Paused = true; Input.Fault(reason);
        }
    }
}
