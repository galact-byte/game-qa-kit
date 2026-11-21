using System;
using System.IO;
using System.Threading;

// Substitute only the OS durability wait; all file bytes and bridge logic remain real.
sealed class DelayedDurableFileStream : FileStream
{
    readonly string path;
    public DelayedDurableFileStream(string path, FileMode mode, FileAccess access, FileShare share)
        : base(path, mode, access, share) { this.path=path; }
    public override void Flush(bool flushToDisk)
    {
        string root=Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_BRIDGE");
        if(path.EndsWith("target-capture-result.json.part",StringComparison.Ordinal))
            while(File.Exists(Path.Combine(root,"fixture.block-receipt-flush")) && !File.Exists(Path.Combine(root,"fixture.stop")))
                Thread.Sleep(5);
        if(flushToDisk && path.EndsWith("target-status.json.part",StringComparison.Ordinal)
            && File.Exists(Path.Combine(Path.GetDirectoryName(path),"fixture.slow-durable-flush")))
            Thread.Sleep(1200);
        base.Flush(flushToDisk);
    }
}
