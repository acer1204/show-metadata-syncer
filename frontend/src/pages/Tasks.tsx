import { useCallback, useEffect, useRef, useState } from "react";
import { Api, FileEntry, TaskStatus, TaskSummary } from "../lib/api";

function selectedFromHash(): string {
  const m = window.location.hash.match(/^#tasks\/(.+)$/);
  return m ? m[1] : "";
}

export default function Tasks() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selected, setSelected] = useState<string>(selectedFromHash);
  const [detail, setDetail] = useState<TaskStatus | null>(null);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [msg, setMsg] = useState("");
  const [seriesId, setSeriesId] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await Api.tasks();
      list.sort((a, b) => b.id.localeCompare(a.id));
      setTasks(list);
    } catch { /* backend down; keep last state */ }
  }, []);

  const refreshDetail = useCallback(async (id: string) => {
    if (!id) { setDetail(null); setFiles([]); return; }
    try {
      const d = await Api.taskStatus(id);
      setDetail(d);
      if (d.status === "done") {
        setFiles(await Api.taskFiles(id));
      } else {
        setFiles([]);
      }
    } catch {
      setDetail(null);
      setFiles([]);
    }
  }, []);

  // 每 2 秒輪詢（有 running 任務或選取的任務未完成時）
  useEffect(() => {
    refresh();
    refreshDetail(selected);
    const t = setInterval(() => {
      refresh();
      if (selected) refreshDetail(selected);
    }, 2000);
    return () => clearInterval(t);
  }, [selected, refresh, refreshDetail]);

  // hash #tasks/{id} 同步
  useEffect(() => {
    const onHash = () => setSelected(selectedFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // log 自動捲到底
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [detail?.logs.length]);

  async function startCrawl() {
    const id = seriesId.trim();
    if (!id) return;
    setMsg("");
    try {
      const { task_id } = await Api.crawl(id);
      setSeriesId("");
      window.location.hash = `#tasks/${task_id}`;
      await refresh();
    } catch (e: any) {
      setMsg("❌ " + (e?.response?.data?.detail ?? e.message));
    }
  }

  async function regenerate() {
    if (!selected) return;
    setMsg("重新產生 NFO 中…");
    try {
      const r = await Api.regenerate(selected);
      setMsg(`✅ 已重產 ${r.files_written.length} 個 NFO`);
      await refreshDetail(selected);
    } catch (e: any) {
      setMsg("❌ " + (e?.response?.data?.detail ?? e.message));
    }
  }

  async function remove(removeOutput: boolean) {
    if (!selected) return;
    const label = removeOutput ? "連同輸出目錄一起刪除" : "從清單移除（保留輸出檔案）";
    if (!window.confirm(`確定要${label}？`)) return;
    try {
      await Api.deleteTask(selected, removeOutput);
      window.location.hash = "#tasks";
      setMsg("已刪除");
      await refresh();
    } catch (e: any) {
      setMsg("❌ " + (e?.response?.data?.detail ?? e.message));
    }
  }

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "32px 24px" }}>
      <header style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 28 }}>🗂️ NFO 爬取任務</h1>
          <p style={{ color: "#8b949e", marginTop: 6 }}>
            每個任務抓完整系列 → 下載圖片 → 產出 Emby 相容 NFO 到 output/
          </p>
        </div>
        <nav style={{ display: "flex", gap: 14, fontSize: 15, paddingTop: 6 }}>
          <a href="#">🔍 查詢</a>
          <a href="#settings">⚙️ 設定</a>
        </nav>
      </header>

      <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
        <input
          value={seriesId}
          onChange={e => setSeriesId(e.target.value)}
          onKeyDown={e => e.key === "Enter" && startCrawl()}
          placeholder="TVDB Series ID（也可從查詢頁的卡片直接啟動）"
          style={{ flex: 1 }}
        />
        <button onClick={startCrawl}>開始爬取</button>
      </div>

      {msg && <div style={{ color: "#8b949e", marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(320px, 1fr) minmax(0, 2fr)", gap: 20 }}>
        {/* 任務清單 */}
        <div style={{ background: "#161b22", border: "1px solid #21262d", borderRadius: 10, overflow: "hidden", alignSelf: "start" }}>
          <table>
            <thead>
              <tr><th>任務</th><th>狀態</th></tr>
            </thead>
            <tbody>
              {tasks.length === 0 && (
                <tr><td colSpan={2} style={{ color: "#6e7681", textAlign: "center" }}>尚無任務</td></tr>
              )}
              {tasks.map(t => (
                <tr key={t.id}
                    onClick={() => { window.location.hash = `#tasks/${t.id}`; }}
                    style={{ cursor: "pointer", background: t.id === selected ? "#1c2532" : undefined }}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{t.title || t.id}</div>
                    <div style={{ color: "#6e7681", fontSize: 12 }}>{t.id}</div>
                  </td>
                  <td><span className={`badge ${t.status}`}>{t.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* 任務詳情 */}
        <div style={{ minWidth: 0 }}>
          {!detail && (
            <div style={{ color: "#6e7681", textAlign: "center", marginTop: 60 }}>
              點左側任務查看 log 與輸出檔案
            </div>
          )}
          {detail && (
            <div style={{ background: "#161b22", border: "1px solid #21262d", borderRadius: 10, padding: 18 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <div>
                  <span style={{ fontWeight: 700, fontSize: 17 }}>{detail.title || detail.id}</span>{" "}
                  <span className={`badge ${detail.status}`} style={{ marginLeft: 8 }}>{detail.status}</span>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  {detail.status === "done" && (
                    <a href={`/api/tasks/${detail.id}/zip`}
                       style={{
                         fontSize: 13, padding: "5px 10px", background: "#238636",
                         color: "white", borderRadius: 6, textDecoration: "none",
                       }}>
                      ⬇️ 下載 ZIP
                    </a>
                  )}
                  <button onClick={regenerate} disabled={detail.status !== "done"}
                          style={{ fontSize: 13, padding: "5px 10px" }}>
                    重產 NFO
                  </button>
                  <button onClick={() => remove(false)}
                          style={{ fontSize: 13, padding: "5px 10px", background: "#30363d" }}>
                    移除任務
                  </button>
                  <button onClick={() => remove(true)}
                          style={{ fontSize: 13, padding: "5px 10px", background: "#a40e26" }}>
                    刪除含輸出
                  </button>
                </div>
              </div>

              {detail.output && (
                <div style={{ color: "#8b949e", fontSize: 13, marginTop: 8, wordBreak: "break-all" }}>
                  輸出:{detail.output}
                </div>
              )}

              <pre ref={logRef} style={{
                background: "#0d1117", border: "1px solid #21262d", borderRadius: 6,
                padding: 12, marginTop: 14, maxHeight: 300, overflow: "auto",
                fontSize: 12.5, lineHeight: 1.6, whiteSpace: "pre-wrap",
              }}>
                {detail.logs.join("\n") || "(尚無 log)"}
              </pre>

              {files.length > 0 && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ color: "#8b949e", fontSize: 13, marginBottom: 8 }}>
                    輸出檔案 ({files.filter(f => !f.is_dir).length})
                  </div>
                  <div style={{ maxHeight: 320, overflow: "auto", border: "1px solid #21262d", borderRadius: 6 }}>
                    <table style={{ fontSize: 13 }}>
                      <tbody>
                        {files.filter(f => !f.is_dir).map(f => (
                          <tr key={f.path}>
                            <td style={{ wordBreak: "break-all" }}>
                              <a href={Api.taskFileUrl(detail.id, f.path)} target="_blank" rel="noreferrer">
                                {f.path}
                              </a>
                            </td>
                            <td style={{ whiteSpace: "nowrap", color: "#6e7681", textAlign: "right" }}>
                              {fmtSize(f.size)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function fmtSize(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}
