/**
 * ContextVault Obsidian companion plugin.
 *
 * Adds four small affordances on top of the vault that ContextVault's
 * Python CLI already maintains:
 *
 *   1. Status bar showing the detected workspace + uncaptured count
 *   2. Command "Open current workspace hot cache"
 *   3. Command "Open Workspace Map canvas"
 *   4. Settings tab to configure the HTTP server URL + bearer token
 *
 * The plugin reads vault files directly via Obsidian's API; the HTTP
 * server is only consulted for derived stats (uncaptured session count).
 * Both fall back gracefully — if the server is unreachable the status
 * bar just shows the workspace id without a count.
 */

import {
  App,
  Plugin,
  PluginSettingTab,
  Setting,
  Notice,
  Modal,
  TFile,
  requestUrl,
} from "obsidian";

interface ContextVaultSettings {
  serverUrl: string;
  bearerToken: string;
  refreshIntervalSeconds: number;
}

const DEFAULT_SETTINGS: ContextVaultSettings = {
  serverUrl: "http://127.0.0.1:7842",
  bearerToken: "",
  refreshIntervalSeconds: 30,
};

interface WorkspaceInfo {
  workspace: string;
  session_count: number;
  updated_at: string | null;
}

export default class ContextVaultPlugin extends Plugin {
  settings!: ContextVaultSettings;
  private statusBarEl: HTMLElement | null = null;
  private refreshHandle: number | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.statusBarEl = this.addStatusBarItem();
    this.statusBarEl.setText("ContextVault: idle");
    this.statusBarEl.addClass("cv-status-bar");

    this.addCommand({
      id: "open-current-workspace-hot",
      name: "Open current workspace hot cache",
      callback: () => this.openCurrentWorkspaceHot(),
    });

    this.addCommand({
      id: "open-workspace-map",
      name: "Open Workspace Map canvas",
      callback: () => this.openCurrentWorkspaceMap(),
    });

    this.addCommand({
      id: "list-workspaces",
      name: "List known workspaces",
      callback: () => this.showWorkspacesModal(),
    });

    this.addSettingTab(new ContextVaultSettingTab(this.app, this));

    void this.refreshStatusBar();
    this.refreshHandle = window.setInterval(
      () => void this.refreshStatusBar(),
      Math.max(5, this.settings.refreshIntervalSeconds) * 1000,
    );
    this.registerInterval(this.refreshHandle);
  }

  onunload(): void {
    if (this.refreshHandle !== null) {
      window.clearInterval(this.refreshHandle);
    }
  }

  // ---- workspace detection ------------------------------------------

  /**
   * Best-effort detection of the "current" workspace inside Obsidian.
   *
   * Obsidian doesn't expose the user's shell PWD. Instead we look at the
   * active file's path: if it lives under ``workspaces/<id>/``, that is
   * the workspace. Otherwise we fall back to the most recently updated
   * workspace from the server (or null if neither resolves).
   */
  async detectWorkspace(): Promise<string | null> {
    const active = this.app.workspace.getActiveFile();
    if (active) {
      const segments = active.path.split("/");
      if (segments.length >= 2 && segments[0] === "workspaces") {
        return segments[1];
      }
    }
    const workspaces = await this.fetchWorkspaces();
    if (workspaces && workspaces.length > 0) {
      return workspaces[0].workspace;
    }
    return null;
  }

  // ---- commands -----------------------------------------------------

  async openCurrentWorkspaceHot(): Promise<void> {
    const ws = await this.detectWorkspace();
    if (!ws) {
      new Notice("ContextVault: no workspace detected.");
      return;
    }
    const path = `workspaces/${ws}/hot.md`;
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile)) {
      new Notice(`ContextVault: ${path} not found.`);
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  async openCurrentWorkspaceMap(): Promise<void> {
    const ws = await this.detectWorkspace();
    if (!ws) {
      new Notice("ContextVault: no workspace detected.");
      return;
    }
    const path = `workspaces/${ws}/Workspace Map.canvas`;
    const file = this.app.vault.getAbstractFileByPath(path);
    if (!(file instanceof TFile)) {
      new Notice(
        `ContextVault: ${path} not found. Run \`contextvault capture\` to generate it.`,
      );
      return;
    }
    await this.app.workspace.getLeaf(false).openFile(file);
  }

  async showWorkspacesModal(): Promise<void> {
    const workspaces = await this.fetchWorkspaces();
    if (!workspaces) {
      new Notice("ContextVault: server unreachable.");
      return;
    }
    new WorkspacesModal(this.app, workspaces).open();
  }

  // ---- status bar ---------------------------------------------------

  async refreshStatusBar(): Promise<void> {
    if (!this.statusBarEl) return;
    const ws = await this.detectWorkspace();
    if (!ws) {
      this.statusBarEl.setText("ContextVault: no workspace");
      return;
    }
    const workspaces = await this.fetchWorkspaces();
    const entry = workspaces?.find((w) => w.workspace === ws);
    if (!entry) {
      this.statusBarEl.setText(`CV: ${ws}`);
      return;
    }
    this.statusBarEl.setText(
      `CV: ${ws} · ${entry.session_count} session${entry.session_count === 1 ? "" : "s"}`,
    );
  }

  // ---- HTTP helpers -------------------------------------------------

  async fetchWorkspaces(): Promise<WorkspaceInfo[] | null> {
    try {
      const resp = await requestUrl({
        url: `${this.settings.serverUrl.replace(/\/$/, "")}/list_workspaces`,
        method: "GET",
        headers: {
          Authorization: `Bearer ${this.settings.bearerToken}`,
        },
        throw: false,
      });
      if (resp.status !== 200) return null;
      const data = resp.json;
      return Array.isArray(data) ? (data as WorkspaceInfo[]) : null;
    } catch {
      return null;
    }
  }

  // ---- settings -----------------------------------------------------

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

class WorkspacesModal extends Modal {
  constructor(app: App, private workspaces: WorkspaceInfo[]) {
    super(app);
  }

  onOpen(): void {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("h2", { text: "ContextVault workspaces" });
    if (this.workspaces.length === 0) {
      contentEl.createEl("p", { text: "No workspaces captured yet." });
      return;
    }
    const table = contentEl.createEl("table", { cls: "cv-workspaces" });
    const head = table.createEl("thead").createEl("tr");
    head.createEl("th", { text: "Workspace" });
    head.createEl("th", { text: "Sessions" });
    head.createEl("th", { text: "Updated" });
    const tbody = table.createEl("tbody");
    for (const w of this.workspaces) {
      const row = tbody.createEl("tr");
      row.createEl("td", { text: w.workspace });
      row.createEl("td", { text: String(w.session_count) });
      row.createEl("td", { text: w.updated_at ?? "—" });
    }
  }

  onClose(): void {
    this.contentEl.empty();
  }
}

class ContextVaultSettingTab extends PluginSettingTab {
  constructor(app: App, private plugin: ContextVaultPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();
    containerEl.createEl("h2", { text: "ContextVault" });

    new Setting(containerEl)
      .setName("Server URL")
      .setDesc("Loopback URL of the contextvault HTTP server.")
      .addText((text) =>
        text
          .setPlaceholder("http://127.0.0.1:7842")
          .setValue(this.plugin.settings.serverUrl)
          .onChange(async (value) => {
            this.plugin.settings.serverUrl = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Bearer token")
      .setDesc(
        "Paste the value of ~/.config/contextvault/token. The plugin reads " +
          "stats from the server; vault files are read via Obsidian directly.",
      )
      .addText((text) =>
        text
          .setPlaceholder("paste token here")
          .setValue(this.plugin.settings.bearerToken)
          .onChange(async (value) => {
            this.plugin.settings.bearerToken = value.trim();
            await this.plugin.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName("Status bar refresh (seconds)")
      .setDesc("How often to refresh the status bar.")
      .addText((text) =>
        text
          .setPlaceholder("30")
          .setValue(String(this.plugin.settings.refreshIntervalSeconds))
          .onChange(async (value) => {
            const n = Number.parseInt(value, 10);
            if (!Number.isNaN(n) && n >= 5) {
              this.plugin.settings.refreshIntervalSeconds = n;
              await this.plugin.saveSettings();
            }
          }),
      );
  }
}
