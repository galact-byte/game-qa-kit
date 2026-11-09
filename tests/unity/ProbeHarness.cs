using BepInEx;
using UnityEngine;

[BepInPlugin("org.gameqakit.unity.probe","Game QA Isolated Probe","0.1.0")]
public sealed class ProbeHarness : BaseUnityPlugin
{
    private void Awake()
    {
        // Loading the fixture assembly here makes it visible before bridge call-site discovery.
        new GameObject("ProbeGame").AddComponent<ProbePlayer>();
    }
}
