import type { AppSettings } from "../types";

export type SourceType = "URL" | "LOCAL_FILE";
export type TextSource = "ASR" | "SUBTITLE";
export type FrameStrategy = "fixed_interval" | "scene_change" | "hybrid";

export interface TaskFormDefaults {
  sourceType: SourceType;
  textSource: TextSource;
  sources: string;
  title: string;
  profileId: string;
  asrGatewayId: string;
  asrGatewayModelId: string;
  ocrEnabled: boolean;
  embeddedCaptionFallback: boolean;
  ocrGatewayId: string;
  ocrGatewayModelId: string;
  textEditEnabled: boolean;
  textModelId: string;
  exportDirectory: string;
  frameStrategy: FrameStrategy;
  frameInterval: number;
  sceneThreshold: number;
  maxCandidateFrames: number;
  fusionWindow: number;
  restored: boolean;
}

export function taskFormDefaults(
  settings: AppSettings,
  snapshot: Record<string, unknown> | null,
  includeInput: boolean,
): TaskFormDefaults {
  const root = asRecord(snapshot);
  const processing = asRecord(root.processing);
  const input = asRecord(root.input);
  const profile = asRecord(root.profile);
  const gateways = asRecord(root.gateways);
  const asr = asRecord(gateways.asr);
  const ocr = asRecord(gateways.ocr);
  const textProcessing = asRecord(root.text_processing);
  const paths = asRecord(root.paths);
  const exportConfig = asRecord(root.export);
  const sourceType = input.source_type === "LOCAL_FILE" ? "LOCAL_FILE" : "URL";
  const textSource = (
    input.text_source === "SUBTITLE" || processing.text_source === "SUBTITLE"
  ) ? "SUBTITLE" : "ASR";
  const rawStrategy = processing.frame_sampling_strategy;
  const frameStrategy: FrameStrategy = (
    rawStrategy === "fixed_interval" || rawStrategy === "scene_change" || rawStrategy === "hybrid"
  ) ? rawStrategy : "hybrid";

  return {
    sourceType,
    textSource,
    sources: includeInput ? stringValue(input.source) : "",
    title: includeInput ? stringValue(input.title) : "",
    profileId: idValue(profile.id ?? settings.last_profile_id),
    asrGatewayId: idValue(asr.gateway_id),
    asrGatewayModelId: idValue(asr.model_id),
    ocrEnabled: booleanValue(processing.ocr_enabled, settings.default_ocr_enabled),
    embeddedCaptionFallback: booleanValue(
      processing.embedded_subtitle_ocr_fallback,
      true,
    ),
    ocrGatewayId: idValue(ocr.gateway_id),
    ocrGatewayModelId: idValue(ocr.model_id),
    textEditEnabled: booleanValue(
      processing.text_edit_enabled,
      settings.default_text_edit_enabled,
    ),
    textModelId: idValue(textProcessing.model_config_id),
    exportDirectory: exportDirectory(settings, paths, exportConfig),
    frameStrategy,
    frameInterval: numberValue(processing.ocr_frame_interval_seconds, 15),
    sceneThreshold: numberValue(processing.scene_change_threshold, 0.35),
    maxCandidateFrames: numberValue(processing.max_candidate_frames, 240),
    fusionWindow: numberValue(processing.fusion_window_seconds, 8),
    restored: snapshot !== null && Object.keys(root).length > 0,
  };
}

function exportDirectory(
  settings: AppSettings,
  paths: Record<string, unknown>,
  exportConfig: Record<string, unknown>,
): string {
  const remembered = stringValue(paths.inbox);
  if (isWindowsAbsolute(remembered)) return remembered;
  const vault = stringValue(exportConfig.vault_path);
  const relative = stringValue(exportConfig.inbox_relative_path) || ".";
  if (isWindowsAbsolute(vault)) {
    return relative === "."
      ? vault.replace(/[\\/]+$/, "")
      : `${vault.replace(/[\\/]+$/, "")}\\${relative.replace(/^[\\/]+/, "")}`;
  }
  if (settings.inbox_path && isWindowsAbsolute(settings.inbox_path)) {
    return settings.inbox_path;
  }
  if (settings.obsidian_vault_path) {
    const fallbackRelative = (settings.inbox_path || "Inbox").replace(/^[\\/]+/, "");
    return `${settings.obsidian_vault_path.replace(/[\\/]+$/, "")}\\${fallbackRelative}`;
  }
  return "";
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function idValue(value: unknown): string {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? String(value)
    : "";
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function isWindowsAbsolute(value: string): boolean {
  return /^(?:[a-z]:[\\/]|\\\\)/i.test(value);
}
