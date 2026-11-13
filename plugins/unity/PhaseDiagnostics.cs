using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Text;

namespace GameQaKit.Unity
{
    // The timer reads managed snapshots only. It never calls Unity or renews a control lease.
    internal sealed class PhaseDiagnostics : IDisposable
    {
        private readonly object gate=new object();
        private readonly string path,session;
        private readonly long origin=Stopwatch.GetTimestamp();
        private readonly Dictionary<string,Completion> completed=new Dictionary<string,Completion>();
        private Timer timer;
        private Scope active;
        private long lastEvent,frame,observation;
        private int writes,writing;
        private volatile bool closed;

        private sealed class Completion
        {
            internal readonly double Duration,Maximum;
            internal Completion(double duration,double maximum) { Duration=duration; Maximum=maximum; }
        }
        private sealed class Scope : IDisposable
        {
            internal readonly PhaseDiagnostics Owner;
            internal readonly Scope Parent;
            internal readonly string Name;
            internal readonly long Start=Stopwatch.GetTimestamp();
            private bool disposed;
            internal Scope(PhaseDiagnostics owner,Scope parent,string name)
            { Owner=owner; Parent=parent; Name=name; }
            public void Dispose()
            {
                if(disposed) return;
                disposed=true;
                Owner.End(this);
            }
        }
        private PhaseDiagnostics(string directory,string identity)
        {
            path=Path.Combine(directory,"diagnostics.json"); session=identity; lastEvent=origin;
            timer=new Timer(Publish,null,Timeout.Infinite,Timeout.Infinite);
            timer.Change(0,250);
        }
        internal static PhaseDiagnostics Open(string directory,string session,bool enabled)
        {
            if(!enabled || session==null || session.Length>128) return null;
            try { return new PhaseDiagnostics(directory,session); }
            catch(Exception) { return null; }
        }
        internal IDisposable Enter(string name,long currentFrame,long currentObservation)
        {
            if(closed || name==null || name.Length>64) return null;
            lock(gate)
            {
                if(closed || (!completed.ContainsKey(name) && completed.Count>=16)) return null;
                frame=currentFrame; observation=currentObservation;
                if(active==null) Complete("between_callbacks",Stopwatch.GetTimestamp()-lastEvent);
                active=new Scope(this,active,name);
                return active;
            }
        }
        private static double Milliseconds(long ticks) { return ticks*1000.0/Stopwatch.Frequency; }
        private void Complete(string name,long elapsed)
        {
            double duration=Milliseconds(elapsed);
            Completion previous;
            completed.TryGetValue(name,out previous);
            if(previous==null && completed.Count>=16) return;
            completed[name]=new Completion(duration,Math.Max(duration,previous==null ? 0 : previous.Maximum));
        }
        internal void RecordDuration(string name,double milliseconds)
        {
            if(name==null || name.Length>64 || milliseconds<0 || double.IsNaN(milliseconds) || double.IsInfinity(milliseconds)) return;
            lock(gate)
            {
                if(closed) return;
                Completion previous;
                completed.TryGetValue(name,out previous);
                if(previous==null && completed.Count>=16) return;
                completed[name]=new Completion(milliseconds,Math.Max(milliseconds,previous==null ? 0 : previous.Maximum));
            }
        }
        private void End(Scope scope)
        {
            lock(gate)
            {
                if(closed) return;
                long now=Stopwatch.GetTimestamp();
                Complete(scope.Name,now-scope.Start);
                if(active==scope) active=scope.Parent;
                lastEvent=now;
            }
        }
        private void Publish(object unused)
        {
            if(closed || Interlocked.CompareExchange(ref writing,1,0)!=0) return;
            try
            {
                Dictionary<string,object> record;
                lock(gate)
                {
                    if(closed || writes>=480 || Milliseconds(Stopwatch.GetTimestamp()-origin)>=120000)
                    { Dispose(); return; }
                    writes++;
                    long now=Stopwatch.GetTimestamp();
                    var phases=new Dictionary<string,object>();
                    foreach(var pair in completed)
                        phases[pair.Key]=new Dictionary<string,object>{{"last_ms",pair.Value.Duration},{"max_ms",pair.Value.Maximum}};
                    record=new Dictionary<string,object>{{"schema_version",1},{"session_id",session},
                        {"sample",writes},{"monotonic_ms",Milliseconds(now-origin)},
                        {"sampled_at",(DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds},
                        {"phase",active==null ? "between_callbacks" : active.Name},
                        {"phase_age_ms",Milliseconds(now-(active==null ? lastEvent : active.Start))},
                        {"frame",frame},{"observation_seq",observation},{"completed",phases}};
                }
                // One snapshot plus its temporary file, at most 8 KiB each; I/O stays off the game thread.
                byte[] data=new UTF8Encoding(false).GetBytes(Wire.Json(record));
                if(data.Length>8192) { Dispose(); return; }
                Wire.Write(path,data);
                if(writes>=480) Dispose();
            }
            catch(Exception) { Dispose(); }
            finally { Interlocked.Exchange(ref writing,0); }
        }
        public void Dispose()
        {
            closed=true;
            var current=timer;
            if(current!=null) current.Dispose();
        }
    }
}
