import { useEffect, useState } from "react";
import { Api, SettingsOut, SettingsPatch, SourceStatus } from "../lib/api";

const LANG_LABELS: Record<string, string> = {
  zhtw: "繁體中文",
  zho: "簡體中文",
  jpn: "日文",
  eng: "英文",
};

export default function Settings() {
  const [eff, setEff] = useState<SettingsOut | null>(null);
  const [searchLang, setSearchLang] = useState("zho");
  const [priority, setPriority] = useState<string[]>(["zhtw", "zho", "jpn", "eng"]);
  const [delay, setDelay] = useState(0.5);
  const [tmdbKey, setTmdbKey] = useState("");
  const [touchedKey, setTouchedKey] = useState(false);
  const [fuzz, setFuzz] = useState(75);
  const [sources, setSources] = useState<SourceStatus[]>([]);
  const [enabled, setEnabled] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function reload() {
    const s = await Api.getSettings();
    setEff(s);
    setSearchLang(s.search_lang);
    setPriority(s.lang_priority);
    setDelay(s.episode_delay);
    setFuzz(s.fuzz_threshold);
    setEnabled(s.enabled_sources);
    setSources(await Api.sources());
    setTmdbKey("");
    setTouchedKey(false);
  }
  useEffect(() => { reload().catch(() => setMsg("無法讀取目前設定")); }, []);

  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= priority.length) return;
    const next = [...priority];
    [next[i], next[j]] = [next[j], next[i]];
    setPriority(next);
  }

  async function save() {
    setBusy(true); setMsg("");
    try {
      const patch: SettingsPatch = {
        search_lang: searchLang,
        lang_priority: priority,
        episode_delay: delay,
        fuzz_threshold: fuzz,
        enabled_sources: enabled,
      };
      // Only include key in payload if user actively typed something in this session
      if (touchedKey) patch.tmdb_api_key = tmdbKey;
      const updated = await Api.updateSettings(patch);
      setEff(updated);
      setEnabled(updated.enabled_sources);
      setSources(await Api.sources());
      setTmdbKey("");
      setTouchedKey(false);
      setMsg("✅ 已儲存,新設定立即生效");
    } catch (e: any) {
      setMsg("❌ 儲存失敗:" + (e?.response?.data?.detail ?? e.message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "32px 24px" }}>
      <header style={{ marginBottom: 24, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24 }}>⚙️ 設定</h1>
          <p style={{ color: "#8b949e", marginTop: 4 }}>
            修改後寫進 <code>data/settings.json</code>,重啟也不會掉
          </p>
        </div>
        <nav style={{ display: "flex", gap: 14, fontSize: 14 }}>
          <a href="#">← 返回查詢</a>
          <a href="#tasks">任務</a>
        </nav>
      </header>

      <Section title="來源">
        <Field label="啟用的 metadata 來源" hint="停用的來源不參與搜尋(source=all 會跳過,指名查詢會回錯誤);立即生效">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {sources.map(s => (
              <label key={s.name} style={{
                display: "flex", alignItems: "center", gap: 10,
                background: "#0d1117", border: "1px solid #30363d", borderRadius: 6,
                padding: "8px 10px", cursor: "pointer",
              }}>
                <input
                  type="checkbox"
                  checked={enabled.includes(s.name)}
                  onChange={e => setEnabled(
                    e.target.checked
                      ? [...enabled, s.name]
                      : enabled.filter(n => n !== s.name)
                  )}
                />
                <span style={{ fontWeight: 600, width: 60 }}>{s.name.toUpperCase()}</span>
                <span style={{ fontSize: 12, color: s.ready ? "#3fb950" : "#d29922" }}>
                  {s.ready ? "✓ 可用" : "⚠ 缺 API Key"}
                </span>
                {s.nfo_crawl && (
                  <span style={{ fontSize: 12, color: "#6e7681" }}>支援 NFO 爬蟲</span>
                )}
              </label>
            ))}
          </div>
        </Field>
      </Section>

      <Section title="語言">
        <Field label="搜尋語言" hint="TheTVDB 搜尋結果標題優先採用的語言">
          <select value={searchLang} onChange={e => setSearchLang(e.target.value)}>
            <option value="zho">中文 (zho)</option>
            <option value="jpn">日文 (jpn)</option>
            <option value="eng">英文 (eng)</option>
          </select>
        </Field>
        <Field label="翻譯優先序" hint="標題 / 簡介挑選翻譯時的優先順序（NFO 與 metadata JSON 都適用）">
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {priority.map((p, i) => (
              <div key={p} style={{
                display: "flex", alignItems: "center", gap: 10,
                background: "#0d1117", border: "1px solid #30363d", borderRadius: 6,
                padding: "6px 10px",
              }}>
                <span style={{ color: "#6e7681", width: 20 }}>{i + 1}.</span>
                <span style={{ flex: 1 }}>{LANG_LABELS[p] ?? p} <code style={{ color: "#6e7681" }}>({p})</code></span>
                <button onClick={() => move(i, -1)} disabled={i === 0}
                        style={{ background: "#30363d", padding: "2px 8px", fontSize: 12 }}>↑</button>
                <button onClick={() => move(i, 1)} disabled={i === priority.length - 1}
                        style={{ background: "#30363d", padding: "2px 8px", fontSize: 12 }}>↓</button>
              </div>
            ))}
          </div>
        </Field>
      </Section>

      <Section title="TMDB">
        <Field
          label="API Key / 讀取權杖"
          hint={eff?.tmdb_api_key_set
            ? "(已設定。要更換才填,留空不變更;送出空字串可清除)"
            : "(尚未設定。免費申請:themoviedb.org 註冊 → 設定 → API。v3 API Key 或 v4 讀取權杖皆可,建議貼 v4 權杖)"}
        >
          <input
            type="password"
            placeholder={eff?.tmdb_api_key_set ? "(目前已設定,要換才填)" : "(貼上你的 TMDB API Key)"}
            value={tmdbKey}
            onChange={e => { setTmdbKey(e.target.value); setTouchedKey(true); }}
            style={{ width: "100%" }}
          />
          {touchedKey && (
            <button
              onClick={() => { setTmdbKey(""); setTouchedKey(true); }}
              style={{ marginTop: 6, background: "#a40e26", fontSize: 12, padding: "4px 10px" }}
            >
              送出時清空 key
            </button>
          )}
        </Field>
      </Section>

      <Section title="匹配">
        <Field label="Fuzz 閾值 (0-100)" hint="模糊匹配的最低分數;不強制套用,給客戶端參考(結果都帶 match_score;API 可帶 min_score 自行過濾)">
          <input
            type="number" min={0} max={100}
            value={fuzz}
            onChange={e => setFuzz(Number(e.target.value))}
            style={{ width: 120 }}
          />
        </Field>
      </Section>

      <Section title="爬蟲">
        <Field label="逐集爬取間隔（秒）" hint="每爬一集詳細頁的等待時間,對 TheTVDB 客氣一點">
          <input
            type="number" min={0} max={10} step={0.1}
            value={delay}
            onChange={e => setDelay(Number(e.target.value))}
            style={{ width: 120 }}
          />
        </Field>
        <Field label="輸出目錄" hint="NFO / 圖片輸出位置(由環境變數 OUTPUT_DIR 控制,唯讀)">
          <code style={{ color: "#8b949e" }}>{eff?.output_dir ?? "…"}</code>
        </Field>
      </Section>

      <div style={{ marginTop: 24, display: "flex", gap: 10, alignItems: "center" }}>
        <button onClick={save} disabled={busy}>{busy ? "儲存中…" : "儲存設定"}</button>
        <button onClick={reload} disabled={busy} style={{ background: "#30363d" }}>還原成目前值</button>
        {msg && <span style={{ color: "#8b949e" }}>{msg}</span>}
      </div>

      {eff && (
        <div style={{ marginTop: 32, padding: 16, background: "#161b22", border: "1px solid #21262d", borderRadius: 8 }}>
          <div style={{ color: "#8b949e", fontSize: 13, marginBottom: 8 }}>目前生效</div>
          <pre style={{ margin: 0, color: "#c9d1d9", fontSize: 12, whiteSpace: "pre-wrap" }}>
{JSON.stringify(eff, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 20 }}>
      <h3 style={{ margin: "0 0 12px", fontSize: 15, color: "#c9d1d9" }}>{title}</h3>
      <div style={{ padding: 16, background: "#161b22", border: "1px solid #21262d", borderRadius: 8 }}>
        {children}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 13, color: "#c9d1d9", marginBottom: 4 }}>{label}</div>
      {children}
      {hint && <div style={{ fontSize: 12, color: "#6e7681", marginTop: 4 }}>{hint}</div>}
    </div>
  );
}
