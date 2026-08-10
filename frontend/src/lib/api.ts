import axios from "axios";

export const api = axios.create({ baseURL: "/api" });

/** Canonical detail schema — every source conforms to this exact shape. */
export interface Person {
  name: string;
  tvdbid?: string;
}

export interface Actor {
  name: string;
  role: string;
  type: string;
  tvdbid: string;
  tmdbid: string;
  thumb: string;
}

export interface Episode {
  id: string;
  number: number;
  season_number: number;
  title: string;
  plot: string;
  overviews: Record<string, string>;
  aired: string;
  runtime: string;
  directors: Person[];
  writers: Person[];
  imdb_id: string;
  tmdb_id: string;
  thumb: string;
  url: string;
}

export interface Season {
  number: number;
  name: string;
  title: string;
  plot: string;
  title_translations: Record<string, string>;
  overviews: Record<string, string>;
  tvdb_id: string;
  from: string;
  to: string;
  episode_count: number;
  poster: string;
  url: string;
  episodes: Episode[];
}

export interface SourceDetail {
  source: string;
  id: string;
  url: string;
  match_score: number;
  media_type: string;
  title: string;
  original_title: string;
  title_translations: Record<string, string>;
  plot: string;
  overviews: Record<string, string>;
  year: string;
  premiered: string;
  end_date: string;
  status: string;
  studio: string;
  runtime: string;
  country: string;
  language: string;
  mpaa: string;
  genres: string[];
  tags: string[];
  rating: { score: number | null; votes: number | null };
  unique_ids: { tvdb: string; imdb: string; tmdb: string; tvrage: string };
  trailers: string[];
  actors: Actor[];
  images: { poster: string; fanart: string; clearlogo: string; banner: string };
  season_count: number | null;
  episode_count: number | null;
  seasons: Season[];
}

export interface MetadataResult {
  query: string;
  sources: SourceDetail[];
}

export interface PreviewItem {
  source: string;
  id: string;
  title_cn: string | null;
  title_native: string | null;
  title_english: string | null;
  year: string | null;
  url: string | null;
  cover: string | null;
  score: number;
  overview: string;
  aliases: string[];
  hint: string;
}

export type EpisodesMode = "none" | "list" | "full";

export interface TaskSummary {
  id: string;
  status: string;
  title: string;
  output: string;
  log_count: number;
}

export interface TaskStatus {
  id: string;
  status: string;
  logs: string[];
  output: string;
  title: string;
}

export interface FileEntry {
  path: string;
  name: string;
  size: number;
  is_dir: boolean;
}

export interface SettingsOut {
  search_lang: string;
  lang_priority: string[];
  episode_delay: number;
  output_dir: string;
  tmdb_api_key_set: boolean;
  fuzz_threshold: number;
  enabled_sources: string[];
}

export interface SourceStatus {
  name: string;
  enabled: boolean;
  ready: boolean;
  requires_key: boolean;
  nfo_crawl: boolean;
}

export interface SettingsPatch {
  search_lang?: string;
  lang_priority?: string[];
  episode_delay?: number;
  tmdb_api_key?: string;      // omit field to leave unchanged; "" to clear
  fuzz_threshold?: number;
  enabled_sources?: string[];
}

export const Api = {
  health: () => api.get<{ status: string }>("/health").then(r => r.data),
  sources: () => api.get<SourceStatus[]>("/sources").then(r => r.data),
  preview: (q: string, source = "all") =>
    api.get<PreviewItem[]>("/preview", { params: { q, source } }).then(r => r.data),
  metadata: (q: string, source = "all", episodes: EpisodesMode = "none") =>
    api.get<MetadataResult>("/metadata", { params: { q, source, episodes } }).then(r => r.data),
  metadataById: (source: string, id: string, episodes: EpisodesMode = "list") =>
    api.get<SourceDetail>(`/metadata/${source}/${id}`, { params: { episodes } }).then(r => r.data),

  crawl: (id: string, source = "tvdb") =>
    api.post<{ task_id: string }>("/crawl", { id, source }).then(r => r.data),
  tasks: () => api.get<TaskSummary[]>("/tasks").then(r => r.data),
  taskStatus: (id: string) => api.get<TaskStatus>(`/status/${id}`).then(r => r.data),
  taskFiles: (id: string) => api.get<FileEntry[]>(`/tasks/${id}/files`).then(r => r.data),
  taskFileUrl: (id: string, path: string) =>
    `/api/tasks/${id}/file?path=${encodeURIComponent(path)}`,
  regenerate: (id: string) =>
    api.post<{ ok: boolean; files_written: string[] }>(`/tasks/${id}/regenerate`).then(r => r.data),
  deleteTask: (id: string, removeOutput: boolean) =>
    api.delete(`/tasks/${id}`, { params: { remove_output: removeOutput } }).then(r => r.data),

  getSettings: () => api.get<SettingsOut>("/settings").then(r => r.data),
  updateSettings: (patch: SettingsPatch) =>
    api.put<SettingsOut>("/settings", patch).then(r => r.data),
};
