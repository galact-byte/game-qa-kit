using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
#if INPUT_SYSTEM
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.UI;
using UnityEngine.InputSystem.LowLevel;
#endif

// This fixture is game code: it uses Unity Input, never the bridge's virtual state.
public sealed class ProbePlayer : MonoBehaviour
{
    private Text label;
    private bool menu;
    private int clicks;
    private GameObject actor;
    private RectTransform actorGraphic;
#if !INPUT_SYSTEM
    private static readonly System.Func<string,float> readAxis=Input.GetAxisRaw;
#endif
#if INPUT_SYSTEM
    private InputAction movement;
    private Keyboard foreign;
#endif
    private void Awake()
    {
        Application.targetFrameRate=30;
        var cameraObject=new GameObject("ProbeCamera");
        var camera=cameraObject.AddComponent<Camera>();
        camera.clearFlags=CameraClearFlags.SolidColor;
        camera.backgroundColor=new Color(0.04f,0.09f,0.18f);
        camera.orthographic=true; camera.orthographicSize=4;
        cameraObject.transform.position=new Vector3(0,0,-10);
        actor=new GameObject("InputControlledPlayer");
        var canvasObject=new GameObject("ProbeCanvas",typeof(Canvas),typeof(CanvasScaler),typeof(GraphicRaycaster));
        canvasObject.GetComponent<Canvas>().renderMode=RenderMode.ScreenSpaceOverlay;
        var actorObject=new GameObject("PlayerGraphic",typeof(RectTransform),typeof(Image));
        actorObject.transform.SetParent(canvasObject.transform,false);
        actorGraphic=(RectTransform)actorObject.transform;
        actorGraphic.anchorMin=actorGraphic.anchorMax=new Vector2(0.5f,0.5f);
        actorGraphic.sizeDelta=new Vector2(60,60);
        actorObject.GetComponent<Image>().color=Color.green;
        var textObject=new GameObject("StateText",typeof(RectTransform),typeof(Text));
        textObject.transform.SetParent(canvasObject.transform,false);
        label=textObject.GetComponent<Text>(); label.font=Resources.GetBuiltinResource<Font>("Arial.ttf");
        label.fontSize=22; label.alignment=TextAnchor.UpperLeft; label.color=Color.white;
        var rect=label.rectTransform; rect.anchorMin=new Vector2(0,1); rect.anchorMax=new Vector2(0,1);
        rect.pivot=new Vector2(0,1); rect.anchoredPosition=new Vector2(24,-24); rect.sizeDelta=new Vector2(580,180);
#if INPUT_SYSTEM
        var eventObject=new GameObject("ProbeEventSystem",typeof(EventSystem),typeof(InputSystemUIInputModule));
        eventObject.GetComponent<InputSystemUIInputModule>().AssignDefaultActions();
        movement=new InputAction("ProbeMove",InputActionType.Value,expectedControlType:"Vector2");
        movement.AddCompositeBinding("2DVector").With("Up","<Keyboard>/upArrow").With("Down","<Keyboard>/downArrow")
            .With("Left","<Keyboard>/leftArrow").With("Right","<Keyboard>/rightArrow");
        movement.Enable();
#else
        Input.simulateMouseWithTouches=false;
        new GameObject("ProbeEventSystem",typeof(EventSystem),typeof(StandaloneInputModule));
#endif
        var buttonObject=new GameObject("ProbeClickButton",typeof(RectTransform),typeof(Image),typeof(Button));
        buttonObject.transform.SetParent(canvasObject.transform,false);
        var buttonRect=(RectTransform)buttonObject.transform;
        buttonRect.anchorMin=new Vector2(0,0); buttonRect.anchorMax=new Vector2(0,0); buttonRect.pivot=Vector2.zero;
        buttonRect.anchoredPosition=new Vector2(24,24); buttonRect.sizeDelta=new Vector2(180,64);
        buttonObject.GetComponent<Image>().color=new Color(0.2f,0.5f,0.9f);
        buttonObject.GetComponent<Button>().onClick.AddListener(()=>clicks++);
        EventSystem.current.SetSelectedGameObject(buttonObject);
        UpdateLabel();
    }
    private void Update()
    {
#if INPUT_SYSTEM
        var keyboard=Keyboard.current;
        if(keyboard!=null && keyboard.name.StartsWith("GameQa") && foreign==null)
            foreign=(Keyboard)InputSystem.AddDevice("Keyboard", "ForeignProbeKeyboard");
        if(foreign!=null) InputSystem.QueueStateEvent(foreign,new KeyboardState(Key.LeftArrow));
        var vector=movement.ReadValue<Vector2>();
        actor.transform.position+=new Vector3(vector.x,vector.y,0)*0.1f;
        if(keyboard!=null && keyboard.enterKey.wasPressedThisFrame) menu=true;
        if(keyboard!=null && keyboard.escapeKey.wasPressedThisFrame) menu=false;
#else
        if(Input.acceleration.sqrMagnitude!=0 || Input.touchCount!=0)
            throw new System.InvalidOperationException("Desktop AI input must leave motion and touch neutral");
        actor.transform.position+=new Vector3(readAxis("Horizontal"),readAxis("Vertical"),0)*0.1f;
        if(Input.GetKeyDown(KeyCode.Return)) menu=true;
        if(Input.GetKeyDown(KeyCode.Escape)) menu=false;
#endif
        UpdateLabel();
    }
    private void UpdateLabel()
    {
        actorGraphic.anchoredPosition=new Vector2(actor.transform.position.x*60,actor.transform.position.y*60);
        label.text="UNITY INPUT PROBE\n"+(menu ? "MENU OPEN" : "MAP")+
            "\nX="+actor.transform.position.x.ToString("F2",System.Globalization.CultureInfo.InvariantCulture)+
            "  Y="+actor.transform.position.y.ToString("F2",System.Globalization.CultureInfo.InvariantCulture)+"\nCLICKS="+clicks;
#if INPUT_SYSTEM
        label.text+="\n"+(foreign!=null && !foreign.enabled && Keyboard.current!=foreign ? "FOREIGN DEVICE ISOLATED" : "DEVICE CHECK PENDING");
#endif
    }
}
