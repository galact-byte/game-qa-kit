using System;
using System.Diagnostics;
using System.Threading;

namespace GameQaKit.Unity
{
    // A single slot includes the completed result until the game thread takes it.
    internal sealed class CapturePublisher : IDisposable
    {
        internal sealed class Publication
        {
            internal readonly string CommandId;
            internal readonly long Frame;
            internal readonly double StartedAt;
            internal Exception Error;
            internal double WriteMilliseconds,ElapsedMilliseconds;
            internal Publication(string commandId,long frame,double startedAt)
            { CommandId=commandId; Frame=frame; StartedAt=startedAt; }
        }
        private readonly object gate=new object();
        private readonly Action<string,byte[]> write;
        private Publication current;
        private bool finished,closed;
        internal CapturePublisher() : this(Wire.Write) { }
        internal CapturePublisher(Action<string,byte[]> writer) { write=writer; }
        internal bool Busy { get { lock(gate) return current!=null; } }
        internal bool Expired(Publication item,double now)
        {
            lock(gate)
                return current==item && finished ? item.ElapsedMilliseconds>2000 : now-item.StartedAt>2;
        }
        internal bool Start(Publication item,byte[] png,string screenshot,string preview)
        {
            long submitted=Stopwatch.GetTimestamp();
            if(png==null || png.Length==0 || png.Length>16*1024*1024)
                throw new ArgumentException("Invalid PNG size");
            lock(gate)
            {
                if(closed || current!=null) return false;
                current=item; finished=false;
            }
            if(!ThreadPool.QueueUserWorkItem(delegate
            {
                long started=Stopwatch.GetTimestamp();
                Exception error=null;
                try
                {
                    if(IsClosed()) return;
                    if(screenshot!=null) write(screenshot,png);
                    if(IsClosed()) return;
                    write(preview,png);
                }
                catch(Exception failure) { error=failure; }
                finally
                {
                    lock(gate)
                    {
                        if(!closed)
                        {
                            item.Error=error;
                            item.WriteMilliseconds=(Stopwatch.GetTimestamp()-started)*1000.0/Stopwatch.Frequency;
                            item.ElapsedMilliseconds=(Stopwatch.GetTimestamp()-submitted)*1000.0/Stopwatch.Frequency;
                            finished=true;
                        }
                    }
                }
            }))
            {
                lock(gate) { current=null; }
                throw new InvalidOperationException("Capture worker unavailable");
            }
            return true;
        }
        private bool IsClosed() { lock(gate) return closed; }
        internal Publication Take()
        {
            lock(gate)
            {
                if(closed || !finished) return null;
                var item=current; current=null; finished=false;
                return item;
            }
        }
        public void Dispose()
        {
            // Do not join a blocked filesystem call on the Unity thread. A late
            // write can leave evidence, but cannot produce a completion or start
            // another target. The owning DesktopProcess Job bounds its lifetime.
            lock(gate) { closed=true; current=null; finished=false; }
        }
    }
}
