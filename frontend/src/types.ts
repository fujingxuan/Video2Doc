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

export type GatewayType = "ASR" | "OCR";

export interface GatewayConfig {
  id: number;
  name: string;
  gateway_type: GatewayType;
  protocol: string;
  base_url: string;
  secret_env_name: string | null;
  secret_configured: boolean;
  enabled: boolean;
  health_status: string;
  last_checked_at: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface GatewayModel {
  id: number;
  gateway_id: number;
  gateway_name: string;
  gateway_type: GatewayType;
  gateway_base_url: string;
  protocol: string;
  model_name: string;
  display_name: string;
  enabled: boolean;
  capabilities: Record<string, unknown>;
  max_chunk_seconds: number | null;
  max_alignment_seconds: number | null;
  supports_native_long_audio: boolean;
  supports_streaming: boolean;
  supports_segment_timestamps: boolean;
  supports_word_timestamps: boolean;
  supports_confidence: boolean;
  supports_language_detection: boolean;
  timestamp_granularity: string[];
  languages: string[];
  health_status: string;
  settings: Record<string, unknown>;
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
  asr_gateway_model_id: number | null;
  ocr_gateway_model_id: number | null;
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
  frames_manifest_path: string | null;
  ocr_timeline_path: string | null;
  fusion_timeline_path: string | null;
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
  frame_summary: {
    candidate_count?: number;
    kept_count?: number;
    dropped_count?: number;
    drop_reasons?: Record<string, number>;
    config?: Record<string, unknown>;
  } | null;
  ocr_preview: {
    provider?: string;
    model?: string;
    frame_count?: number;
    reused_count?: number;
    block_count?: number;
    elapsed_seconds?: number;
    text?: string;
    truncated?: boolean;
  } | null;
  fusion_preview: {
    event_count?: number;
    counts?: Record<string, number>;
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
