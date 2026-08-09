import { useEffect, useState } from "react";
import { Api, MetadataResult, SourceDetail } from "../lib/api";

const ACCENT: Record<string, string> = {
  tvdb: "#6cd491",
  tmdb: "#01b4e4",
};

export default function Lookup() {
  const [q, setQ] = useState("");
  const [result, setResult] = useState<MetadataResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function search(term = q.trim()) {
    if (!term) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      setResult(await Api.metadata(term));
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e.message ?? "查詢失敗");
    } finally {
      setLoading(false);
    }
  }

  // 支援 ?q= 深連結：開頁自動帶入並查詢
  useEffect(() => {
    const initial = new URLSearchParams(window.location.search).get("q");
    if (initial) {
      setQ(initial);
      search(initial);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "32px 24px" }}>
      <header style={{ marginBottom: 32, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 28 }}>📺 電視節目 Metadata 查詢</h1>
          <p style={{ color: "#8b949e", marginTop: 6 }}>
            查 TheTVDB（可擴充其他來源），所有來源使用相同 schema，可輸出 Emby NFO
          </p>
        </div>
        <nav style={{ display: "flex", gap: 14, fontSize: 15, paddingTop: 6 }}>
          <a href="#tasks">🗂️ 任務</a>
          <a href="#settings">⚙️ 設定</a>
        </nav>
      </header>

      <div style={{ display: "flex", gap: 10, marginBottom: 24 }}>
        <input
          autoFocus
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === "Enter" && search()}
          placeholder="例如:女王之刃、一騎当千、Queen's Blade、或 TVDB ID"
          style={{ flex: 1, fontSize: 16, padding: "10px 14px" }}
        />
        <button onClick={() => search()} disabled={loading} style={{ fontSize: 15, padding: "10px 22px" }}>
          {loading ? "查詢中…" : "查詢"}
        </button>
      </div>

      {error && (
        <div style={{ background: "#3d1a1d", border: "1px solid #a40e26", padding: 12, borderRadius: 6, marginBottom: 16 }}>
          ❌ {error}
        </div>
      )}

      {result && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(420px, 1fr))",
          gap: 20,
        }}>
          {result.sources.map((d, i) => (
            <SourceCard key={`${d.source}-${d.id}-${i}`} d={d} />
          ))}
        </div>
      )}

      {!result && !loading && !error && (
        <div style={{ color: "#6e7681", textAlign: "center", marginTop: 80 }}>
          輸入節目名稱後按 Enter
        </div>
      )}
    </div>
  );
}

function SourceCard({ d }: { d: SourceDetail }) {
  const accent = ACCENT[d.source] ?? "#8b949e";
  const label = d.source.toUpperCase();
  const [crawlMsg, setCrawlMsg] = useState("");

  if (!d.id) {
    return (
      <div style={{
        background: "#0d1117", border: "1px dashed #30363d", borderRadius: 10,
        padding: 40, textAlign: "center", color: "#6e7681",
      }}>
        {label} 沒有找到結果
      </div>
    );
  }

  async function startCrawl() {
    setCrawlMsg("啟動中…");
    try {
      const { task_id } = await Api.crawl(d.id);
      window.location.hash = `#tasks/${task_id}`;
    } catch (e: any) {
      setCrawlMsg("❌ " + (e?.response?.data?.detail ?? e.message));
    }
  }

  const ids = d.unique_ids;

  return (
    <div style={{
      background: "#161b22", border: "1px solid #21262d", borderRadius: 10,
      borderTop: `4px solid ${accent}`, padding: 18, overflow: "hidden",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14, gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <h3 style={{ margin: 0, color: accent }}>{label}</h3>
          <span title="模糊比對分數" style={{
            background: "#21262d", color: scoreColor(d.match_score),
            padding: "2px 8px", borderRadius: 10, fontSize: 12, fontWeight: 600,
          }}>
            {d.match_score.toFixed(1)}
          </span>
        </div>
        <div style={{ display: "flex", gap: 12, fontSize: 13, flexShrink: 0 }}>
          <a href={`/api/metadata/${d.source}/${d.id}?episodes=list`} target="_blank" rel="noreferrer">JSON</a>
          <a href={d.url} target="_blank" rel="noreferrer">原站連結 →</a>
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
        {d.images.poster && (
          <img src={d.images.poster} referrerPolicy="no-referrer" alt=""
               style={{ width: 120, height: 170, objectFit: "cover", borderRadius: 6, flexShrink: 0 }} />
        )}
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 18, fontWeight: 700, lineHeight: 1.3, wordBreak: "break-word" }}>
            {d.title || "(無標題)"}
          </div>
          {d.original_title && d.original_title !== d.title && (
            <div style={{ fontSize: 14, color: "#8b949e", marginTop: 4, wordBreak: "break-word" }}>
              {d.original_title}
            </div>
          )}
          <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button onClick={startCrawl} style={{ fontSize: 13, padding: "6px 12px" }}>
              📦 產生 NFO + 下載圖片
            </button>
          </div>
          {crawlMsg && <div style={{ marginTop: 6, fontSize: 12, color: "#8b949e" }}>{crawlMsg}</div>}
        </div>
      </div>

      <Row label="年份" value={d.year} />
      <Row label="首播" value={d.premiered} />
      <Row label="完結" value={d.end_date} />
      <Row label="狀態" value={d.status} />
      <Row label="電視台" value={d.studio} />
      <Row label="單集長度" value={d.runtime ? `${d.runtime} 分` : ""} />
      <Row label="分級" value={d.mpaa} />
      <Row label="季 / 集" value={fmtCounts(d.season_count, d.episode_count)} />
      <Row label="評分" value={d.rating.score != null ? `${d.rating.score} / 10` : ""} />
      <Row label="外部 ID" value={fmtIds(ids)} />

      <Block label="類型">
        <Chips items={d.genres} />
      </Block>

      <Block label="標籤">
        <Chips items={d.tags} />
      </Block>

      <Block label="簡介">
        {d.plot ? (
          <p style={{ whiteSpace: "pre-wrap", margin: 0, lineHeight: 1.7, color: "#c9d1d9" }}>
            {d.plot}
          </p>
        ) : (
          <span style={{ color: "#6e7681" }}>—</span>
        )}
      </Block>

      {d.actors.length > 0 && (
        <Block label={`演員 (${d.actors.length})`}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {d.actors.slice(0, 10).map((a, i) => (
              <span key={i} title={a.role} style={{
                background: "#21262d", color: "#c9d1d9", padding: "3px 9px",
                borderRadius: 12, fontSize: 12,
              }}>
                {a.name}{a.role ? ` (${a.role})` : ""}
              </span>
            ))}
            {d.actors.length > 10 && (
              <span style={{ color: "#6e7681", fontSize: 12, padding: "3px 0" }}>
                …等 {d.actors.length} 位
              </span>
            )}
          </div>
        </Block>
      )}

      {d.seasons.length > 0 && (
        <Block label="季列表">
          <table style={{ fontSize: 13 }}>
            <thead>
              <tr><th>季</th><th>名稱</th><th>集數</th><th>期間</th></tr>
            </thead>
            <tbody>
              {d.seasons.map(s => (
                <tr key={s.number}>
                  <td>{s.number === 0 ? "特別篇" : s.number}</td>
                  <td>{s.title || s.name}</td>
                  <td>{s.episode_count}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{s.from}{s.to ? ` ~ ${s.to}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Block>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  const empty = !value;
  return (
    <div style={{ display: "flex", gap: 12, padding: "6px 0", fontSize: 14 }}>
      <span style={{ color: "#8b949e", width: 90, flexShrink: 0 }}>{label}</span>
      <span style={{ color: empty ? "#6e7681" : "#e6edf3", wordBreak: "break-word" }}>{empty ? "—" : value}</span>
    </div>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid #21262d" }}>
      <div style={{ color: "#8b949e", fontSize: 13, marginBottom: 8 }}>{label}</div>
      {children}
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  if (!items.length) return <span style={{ color: "#6e7681" }}>—</span>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {items.map((t, i) => (
        <span key={i} style={{
          background: "#21262d", color: "#c9d1d9", padding: "3px 9px",
          borderRadius: 12, fontSize: 12,
        }}>{t}</span>
      ))}
    </div>
  );
}

function scoreColor(s: number): string {
  if (s >= 90) return "#3fb950";
  if (s >= 75) return "#d29922";
  return "#f85149";
}

function fmtCounts(seasons: number | null, episodes: number | null): string {
  const parts: string[] = [];
  if (seasons) parts.push(`${seasons} 季`);
  if (episodes) parts.push(`${episodes} 集`);
  return parts.join(" · ");
}

function fmtIds(ids: { tvdb: string; imdb: string; tmdb: string; tvrage: string }): string {
  const parts: string[] = [];
  if (ids.tvdb) parts.push(`tvdb:${ids.tvdb}`);
  if (ids.imdb) parts.push(`imdb:${ids.imdb}`);
  if (ids.tmdb) parts.push(`tmdb:${ids.tmdb}`);
  if (ids.tvrage) parts.push(`tvrage:${ids.tvrage}`);
  return parts.join(" · ");
}
