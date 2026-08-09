import { describe, expect, it } from "vitest";

import type { AppSettings } from "../types";
import { taskFormDefaults } from "./taskDefaults";

const settings = {
  last_profile_id: 3,
  inbox_path: "D:\\Notes\\Inbox",
  obsidian_vault_path: "D:\\Notes",
  default_ocr_enabled: false,
  default_text_edit_enabled: false,
} as AppSettings;

describe("taskFormDefaults", () => {
  it("restores the complete previous task setup without copying its source", () => {
    const defaults = taskFormDefaults(settings, {
      profile: { id: 9 },
      input: {
        source: "https://example.invalid/previous",
        source_type: "URL",
        title: "Previous",
        text_source: "SUBTITLE",
      },
      processing: {
        text_source: "SUBTITLE",
        ocr_enabled: true,
        embedded_subtitle_ocr_fallback: false,
        text_edit_enabled: true,
        frame_sampling_strategy: "scene_change",
        ocr_frame_interval_seconds: 22,
        scene_change_threshold: 0.42,
        max_candidate_frames: 99,
        fusion_window_seconds: 12,
      },
      gateways: {
        asr: { gateway_id: 4, model_id: 5 },
        ocr: { gateway_id: 6, model_id: 7 },
      },
      text_processing: { model_config_id: 8 },
      paths: { inbox: "E:\\Exports" },
    }, false);

    expect(defaults).toMatchObject({
      sourceType: "URL",
      textSource: "SUBTITLE",
      sources: "",
      title: "",
      profileId: "9",
      asrGatewayId: "4",
      asrGatewayModelId: "5",
      ocrEnabled: true,
      embeddedCaptionFallback: false,
      ocrGatewayId: "6",
      ocrGatewayModelId: "7",
      textEditEnabled: true,
      textModelId: "8",
      exportDirectory: "E:\\Exports",
      frameStrategy: "scene_change",
      frameInterval: 22,
      sceneThreshold: 0.42,
      maxCandidateFrames: 99,
      fusionWindow: 12,
      restored: true,
    });
  });

  it("includes the source and title while editing that queued task", () => {
    const defaults = taskFormDefaults(settings, {
      input: {
        source: "D:\\Media\\lesson.mp4",
        source_type: "LOCAL_FILE",
        title: "Lesson",
      },
    }, true);

    expect(defaults.sourceType).toBe("LOCAL_FILE");
    expect(defaults.sources).toBe("D:\\Media\\lesson.mp4");
    expect(defaults.title).toBe("Lesson");
  });
});
