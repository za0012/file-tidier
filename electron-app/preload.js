const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fileTidier", {
  chooseFolder: () => ipcRenderer.invoke("choose-folder"),
  chooseReferenceZip: () => ipcRenderer.invoke("choose-reference-zip"),
  openManager: () => ipcRenderer.invoke("open-manager"),
  openTidier: () => ipcRenderer.invoke("open-tidier"),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  loadManagerStore: () => ipcRenderer.invoke("manager-store-load"),
  saveManagerStore: (data) => ipcRenderer.invoke("manager-store-save", data),
  getAppIntegrity: () => ipcRenderer.invoke("app-integrity"),
  quarantineFiles: (options) => ipcRenderer.invoke("quarantine-files", options),
  compareItems: (options) => ipcRenderer.invoke("compare-items", options),
  applyRename: (options) => ipcRenderer.invoke("apply-rename", options),
  fetchWebCovers: (options) => ipcRenderer.invoke("web-covers", options),
  scan: (options) => ipcRenderer.invoke("scan", options),
  saveScanResult: (options) => ipcRenderer.invoke("save-scan-result", options),
  loadScanResult: () => ipcRenderer.invoke("load-scan-result"),
  cancelScan: () => ipcRenderer.invoke("cancel-scan"),
  onScanProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("scan-progress", listener);
    return () => ipcRenderer.removeListener("scan-progress", listener);
  },
});
