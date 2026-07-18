export type Capability = "ASR" | "OCR" | "TEXT_PROCESSING" | "VISION";
export type TaskStatus =
  | "QUEUED"
  | "PROCESSING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "INTERRUPTED";

export interface ModelConfig {
  id: number;
  name: string;
  capabilities: Capability[];
  notes: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProcessingProfile {
  id: number;
  name: string;
  description: string | null;
  enabled: boolean;
  is_builtin: boolean;
  is_default: boolean;
  asr_model_id: number | null;
  ocr_model_id: number | null;
  text_model_id: number | null;
  ocr_enabled: boolean;
  remove_fillers: boolean;
  fix_sentences: boolean;
  auto_paragraphs: boolean;
  generate_headings: boolean;
  keep_timestamps: boolean;
  keep_raw_transcript: boolean;
  keep_downloaded_media: boolean;
  ocr_frame_interval_seconds: number;
  markdown_template: string | null;
  output_directory: string | null;
  data_directory: string | null;
  created_at: string;
  updated_at: string;
}

export interface StageRun {
  id: number;
  attempt: number;
  stage: string;
  status: string;
  progress: number;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  technical_error: string | null;
  input_artifacts: string[] | null;
  output_artifacts: string[] | null;
  log_path: string | null;
}

export interface Task {
  id: number;
  source: string;
  source_type: string;
  title: string;
  status: TaskStatus;
  current_stage: string;
  progress: number;
  cancel_requested: boolean;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  workspace_path: string | null;
  output_path: string | null;
  media_info_path: string | null;
  transcript_path: string | null;
  artifacts_index_path: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface TaskDetail extends Task {
  snapshot: {
    schema_version: number;
    snapshot_json: Record<string, unknown>;
    created_at: string;
  };
  stages: StageRun[];
  media_info: {
    title?: string | null;
    duration?: number | null;
    container?: string | null;
    has_audio?: boolean;
    has_video?: boolean;
    audio_streams?: Array<Record<string, unknown>>;
    video_streams?: Array<Record<string, unknown>>;
  } | null;
  transcript_preview: {
    provider?: string;
    model?: string;
    language?: string | null;
    chunk_count?: number;
    completed_chunks?: number;
    segment_count?: number;
    text?: string;
    truncated?: boolean;
  } | null;
  artifacts: Array<{ path: string; size: number }>;
}

export interface QueueState {
  max_concurrency: number;
  running: number;
  queued: number;
  completed: number;
  failed: number;
  cancelled: number;
  interrupted: number;
  running_task_id: number | null;
}

export interface AppSettings {
  id: number;
  oneapi_base_url: string | null;
  api_key_configured: boolean;
  obsidian_vault_path: string | null;
  inbox_path: string | null;
  data_path: string | null;
  ffmpeg_path: string | null;
  ytdlp_path: string | null;
  ocr_command: string | null;
  last_profile_id: number | null;
  last_task_config: Record<string, unknown> | null;
  default_markdown_template: string | null;
  default_ocr_enabled: boolean;
  default_keep_raw_transcript: boolean;
  default_keep_timestamps: boolean;
  default_keep_downloaded_media: boolean;
  default_keep_frames: boolean;
  updated_at: string;
}
