const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("bookshelf", {
  load: () => ipcRenderer.invoke("shelf:load"),
  save: (data) => ipcRenderer.invoke("shelf:save", data),
  enrichBook: (bookId) => ipcRenderer.invoke("shelf:enrichBook", bookId),
  scrape: (categories) => ipcRenderer.invoke("shelf:scrape", categories),
  scrapeEvent: () => ipcRenderer.invoke("shelf:scrapeEvent"),
  scrapeAuthors: (payload) => ipcRenderer.invoke("shelf:scrapeAuthors", payload),
  onProgress: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("shelf:progress", listener);
    return () => ipcRenderer.removeListener("shelf:progress", listener);
  },
  openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url)
});

