// LocalTC's toolbar panel in the sim (for VR): the app's own panel page, from the app on this PC (its panel port,
// [ui] panel_port, 47810 unless changed). Loaded when the panel opens, emptied when it closes (nothing runs hidden).
// After bbrks/msfs2020-toolbar-theflightsimkit (MIT).
var LOCALTC_URL = "http://127.0.0.1:47810/static/panel.html";

class IngamePanelLocalTC extends TemplateElement {
    constructor() {
        super(...arguments);
        this.ingameUi = null;
        this.iframeElement = null;
    }
    connectedCallback() {
        super.connectedCallback();
        var self = this;
        this.ingameUi = this.querySelector("ingame-ui");
        this.iframeElement = document.getElementById("LocalTCPanelIframe");
        if (this.ingameUi) {
            this.ingameUi.addEventListener("panelActive", function () {
                if (self.iframeElement) {
                    self.iframeElement.src = LOCALTC_URL + "?t=" + Date.now();
                }
            });
            this.ingameUi.addEventListener("panelInactive", function () {
                if (self.iframeElement) {
                    self.iframeElement.src = "";
                }
            });
        }
    }
    disconnectedCallback() {
        super.disconnectedCallback();
    }
}
window.customElements.define("ingamepanel-localtc", IngamePanelLocalTC);
checkAutoload();
