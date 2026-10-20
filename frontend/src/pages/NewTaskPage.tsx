import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { Icon } from "../components/Icon";
import type {
  GatewayModel,
  ModelConfig,
  ProcessingProfile,
} from "../types";
import { taskFormDefaults } from "../utils/taskDefaults";

interface Props {
  profiles: ProcessingProfile[];
  gatewayModels: GatewayModel[];
  models: ModelConfig[];
  onCreated: () => Promise<void>;
  editTaskId?: number;
}

export function NewTaskPage({
  profiles,
  gatewayModels,
  models,
  onCreated,
  editTaskId,
}: Props) {
  const [sourceType, setSourceType] = useState<"URL" | "LOCAL_FILE" | "UPLOAD">("URL");
  const [textSource, setTextSource] = useState<"ASR" | "SUBTITLE">("ASR");
  const [sources, setSources] = useState("");
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploadKey, setUploadKey] = useState(0);
  const [title, setTitle] = useState("");
  const [profileId, setProfileId] = useState("");
  const [asrGatewayId, setAsrGatewayId] = useState("");
  const [asrGatewayModelId, setAsrGatewayModelId] = useState("");
  const [ocrEnabled, setOcrEnabled] = useState(false);
  const [embeddedCaptionFallback, setEmbeddedCaptionFallback] = useState(true);
  const [ocrGatewayId, setOcrGatewayId] = useState("");
  const [ocrGatewayModelId, setOcrGatewayModelId] = useState("");
  const [textEditEnabled, setTextEditEnabled] = useState(false);
  const [textModelId, setTextModelId] = useState("");
  const [exportDirectory, setExportDirectory] = useState("");
  const [frameStrategy, setFrameStrategy] = useState<
    "fixed_interval" | "scene_change" | "hybrid"
  >("hybrid");
  const [frameInterval, setFrameInterval] = useState(15);
  const [sceneThreshold, setSceneThreshold] = useState(0.35);
  const [maxCandidateFrames, setMaxCandidateFrames] = useState(240);
  const [fusionWindow, setFusionWindow] = useState(8);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [pickingOutput, setPickingOutput] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setInitializing(true);
    Promise.all([
      api.settings(),
      editTaskId ? api.task(editTaskId) : Promise.resolve(null),
    ])
      .then(([settings, task]) => {
        if (!active) return;
        const defaults = taskFormDefaults(
          settings,
          task?.snapshot.snapshot_json ?? settings.last_task_config,
          Boolean(task),
        );
        setSourceType(defaults.sourceType);
        setTextSource(defaults.textSource);
        setSources(defaults.sources);
        setTitle(defaults.title);
        setProfileId(defaults.profileId);
        setAsrGatewayId(defaults.asrGatewayId);
        setAsrGatewayModelId(defaults.asrGatewayModelId);
        setOcrEnabled(defaults.ocrEnabled);
        setEmbeddedCaptionFallback(defaults.embeddedCaptionFallback);
        setOcrGatewayId(defaults.ocrGatewayId);
        setOcrGatewayModelId(defaults.ocrGatewayModelId);
        setTextEditEnabled(defaults.textEditEnabled);
        setTextModelId(defaults.textModelId);
        setExportDirectory(defaults.exportDirectory);
        setFrameStrategy(defaults.frameStrategy);
        setFrameInterval(defaults.frameInterval);
        setSceneThreshold(defaults.sceneThreshold);
        setMaxCandidateFrames(defaults.maxCandidateFrames);
        setFusionWindow(defaults.fusionWindow);
        if (task && task.status !== "QUEUED") {
          setMessage("该任务已经开始处理，只能查看，不能再修改队列配置。");
        } else if (!task && defaults.restored) {
          setMessage("已自动恢复上一个任务的处理方案、模型、OCR、文本整理与导出设置。");
        } else {
          setMessage(null);
        }
      })
      .catch(error => {
        if (active) setMessage(error instanceof Error ? error.message : "设置加载失败");
      })
      .finally(() => {
        if (active) setInitializing(false);
      });
    return () => { active = false; };
  }, [editTaskId]);

  const asrGateways = useMemo(
    () => Array.from(new Map(
      gatewayModels
        .filter(item => item.gateway_type === "ASR" && item.enabled)
        .map(item => [item.gateway_id, item.gateway_name]),
    ).entries()),
    [gatewayModels],
  );
  const asrModels = useMemo(
    () => gatewayModels.filter(
      item => item.gateway_type === "ASR"
        && item.enabled
        && String(item.gateway_id) === asrGatewayId,
    ),
    [asrGatewayId, gatewayModels],
  );
  const ocrGateways = useMemo(
    () => Array.from(new Map(
      gatewayModels
        .filter(item => item.gateway_type === "OCR" && item.enabled)
        .map(item => [item.gateway_id, item.gateway_name]),
    ).entries()),
    [gatewayModels],
  );
  const ocrModels = useMemo(
    () => gatewayModels.filter(
      item => item.gateway_type === "OCR"
        && item.enabled
        && String(item.gateway_id) === ocrGatewayId,
    ),
    [gatewayModels, ocrGatewayId],
  );
  const textModels = useMemo(
    () => models.filter(
      item => item.enabled && item.capabilities.includes("TEXT_PROCESSING"),
    ),
    [models],
  );
  const selectedProfile = useMemo(
    () => profiles.find(item => String(item.id) === profileId),
    [profileId, profiles],
  );
  const selectedTextModel = useMemo(
    () => models.find(item => item.id === (
      textModelId ? Number(textModelId) : selectedProfile?.text_model_id
    )),
    [models, selectedProfile, textModelId],
  );
  const ocrRequired = ocrEnabled || (
    textSource === "SUBTITLE" && embeddedCaptionFallback
  );

  const pickFiles = async () => {
    setPicking(true);
    setMessage(null);
    try {
      const result = await api.selectMedia();
      if (result.paths.length) {
        setSourceType("LOCAL_FILE");
        setSources(result.paths.join("\n"));
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法打开文件选择器");
    } finally {
      setPicking(false);
    }
  };

  const pickOutput = async () => {
    setPickingOutput(true);
    setMessage(null);
    try {
      const result = await api.selectOutputDirectory();
      if (result.path) setExportDirectory(result.path);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "无法打开导出目录选择器");
    } finally {
      setPickingOutput(false);
    }
  };

  const submit = async () => {
    const inputs = sources.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
    if (sourceType === "UPLOAD" ? !uploadFiles.length : !inputs.length) {
      setMessage("请至少选择或输入一个视频、音频或媒体 URL。");
      return;
    }
    if (editTaskId && inputs.length !== 1) {
      setMessage("编辑队列任务时只能保留一个输入来源；批量任务请分别编辑。");
      return;
    }
    if (sourceType === "URL" && inputs.some(value => !/^https?:\/\//i.test(value))) {
      setMessage("URL 输入必须使用 http 或 https。");
      return;
    }
    if (
      sourceType === "LOCAL_FILE"
      && inputs.some(value => !/^(?:[a-z]:[\\/]|\\\\)/i.test(value))
    ) {
      setMessage("本地媒体必须使用 Windows 绝对路径。");
      return;
    }
    if (textSource === "SUBTITLE" && sourceType !== "URL") {
      setMessage("从视频链接抓取字幕仅支持视频网页 URL。");
      return;
    }
    if (ocrRequired && !ocrGatewayModelId && !selectedProfile?.ocr_gateway_model_id) {
      setMessage("识别画面文字或内嵌字幕时必须选择 OCR Gateway 模型，或使用已配置 OCR 的处理方案。");
      return;
    }
    if (textEditEnabled && !textModelId && !selectedProfile?.text_model_id) {
      setMessage("启用轻度文本整理时必须选择 TEXT_PROCESSING 模型。");
      return;
    }
    if (textEditEnabled && !selectedTextModel?.base_url?.trim()) {
      setMessage("启用轻度文本整理前必须在 LLM 页面为所选模型配置 Base URL。");
      return;
    }
    if (
      textEditEnabled
      && selectedTextModel?.secret_source !== "NONE"
      && !selectedTextModel?.secret_configured
    ) {
      setMessage("所选 LLM 的密钥尚不可用；请检查环境变量或重新直接填写密钥。");
      return;
    }
    if (exportDirectory && !/^(?:[a-z]:[\\/]|\\\\)/i.test(exportDirectory)) {
      setMessage("导出目录必须填写 Windows 绝对路径。");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const configuration = {
        pipeline_mode: "REAL",
        profile_id: profileId ? Number(profileId) : null,
        asr_gateway_model_id: asrGatewayModelId
          ? Number(asrGatewayModelId)
          : null,
        ocr_gateway_model_id: ocrGatewayModelId
          ? Number(ocrGatewayModelId)
          : null,
        text_model_id: textModelId ? Number(textModelId) : null,
        export_directory: exportDirectory || null,
        config_overrides: {
          ocr_enabled: ocrEnabled,
          embedded_subtitle_ocr_fallback: textSource === "SUBTITLE" && embeddedCaptionFallback,
          embedded_subtitle_interval_seconds: 1,
          text_edit_enabled: textEditEnabled,
          frame_sampling_strategy: frameStrategy,
          ocr_frame_interval_seconds: frameInterval,
          scene_change_threshold: sceneThreshold,
          max_candidate_frames: maxCandidateFrames,
          fusion_window_seconds: fusionWindow,
          keep_timestamps: false,
        },
      };
      const taskInputs = inputs.map(source => ({
        source,
        source_type: sourceType,
        text_source: textSource,
        ...(editTaskId ? { title: title.trim() || null } : {}),
      }));
      if (editTaskId) {
        await api.updateTask(editTaskId, { ...configuration, input: taskInputs[0] });
        setMessage("队列任务设置已更新。");
      } else {
        const tasks = sourceType === "UPLOAD"
          ? await api.uploadAudio(uploadFiles, configuration)
          : await api.createTasks({ ...configuration, inputs: taskInputs });
        setUploadFiles([]); setUploadKey(value => value + 1);
        setSources("");
        setMessage(`已创建 ${tasks.length} 个完整转换任务。`);
      }
      await onCreated();
      if (editTaskId) window.location.hash = "/queue";
    } catch (error) {
      setMessage(error instanceof Error ? error.message : (editTaskId ? "保存失败" : "创建失败"));
    } finally {
      setBusy(false);
    }
  };

  return <>
    <header className="page-header">
      <div>
        <p className="eyebrow">{editTaskId ? "仅限尚未开始的任务" : "视频 / 音频 / 网页 URL"}</p>
        <h1>{editTaskId ? "编辑队列任务" : "新建转换任务"}</h1>
        <p>{editTaskId
          ? "保存后会更新输入来源和完整配置快照；任务一旦开始处理就不再允许修改。"
          : "自动沿用上一个任务的处理设置；只需填写新的输入来源即可加入队列。"}</p>
      </div>
    </header>
    <section className="panel form-panel new-task-form" aria-busy={initializing}>
      <div className="form-section-heading"><span>01</span><div><h2>添加内容来源</h2><p>支持批量链接、本地视频与音频文件</p></div></div>
      {editTaskId && <label>任务标题
        <input
          maxLength={300}
          value={title}
          onChange={event => setTitle(event.target.value)}
          placeholder="留空时根据输入来源自动生成"
        />
      </label>}
      <div className="segmented" aria-disabled={busy}>
        <button
          disabled={busy} className={sourceType === "URL" ? "selected" : ""}
          onClick={() => { setSourceType("URL"); setSources(""); }}
        >
          视频网页 URL
        </button>
        <button
          disabled={busy} className={sourceType === "LOCAL_FILE" ? "selected" : ""}
          onClick={() => {
            setSourceType("LOCAL_FILE");
            setTextSource("ASR");
            setSources("");
          }}
        >
          本地视频 / 音频路径
        </button>
        {!editTaskId && <button disabled={busy} className={sourceType === "UPLOAD" ? "selected" : ""}
          onClick={() => { setSourceType("UPLOAD"); setTextSource("ASR"); setOcrEnabled(false); setSources(""); }}>
          上传本地音频
        </button>}
      </div>
      {sourceType === "LOCAL_FILE" && <button onClick={() => void pickFiles()} disabled={picking}>
        {picking ? "等待选择…" : "选择本地媒体文件…"}
      </button>}
      {sourceType !== "UPLOAD" && <label>
        {sourceType === "URL" ? "媒体 URL（每行一个）" : "本地媒体绝对路径（每行一个）"}
        <textarea
          rows={7}
          value={sources}
          onChange={event => setSources(event.target.value)}
          placeholder={sourceType === "URL"
            ? "https://www.youtube.com/watch?v=...\nhttps://www.bilibili.com/video/..."
            : "D:\\Media\\lecture.mp4\nD:\\Media\\audio.m4a"}
        />
      </label>}
      {sourceType === "UPLOAD" && <label className="audio-upload">选择音频文件
        <input key={uploadKey} type="file" multiple disabled={busy}
          accept=".wav,.mp3,.m4a,.aac,.flac,.ogg,.opus,.wma,.aiff,.aif,.amr"
          onChange={event => {
            const files = Array.from(event.target.files ?? []);
            if (files.length > 20 || files.some(file => file.size > 2 * 1024 ** 3 || file.size === 0)) {
              setMessage("每次最多上传 20 个音频文件，单个文件需大于 0 且不超过 2 GiB。");
              setUploadFiles([]); event.target.value = ""; return;
            }
            setUploadFiles(files); setMessage(null);
          }} />
        <small>支持 WAV、MP3、M4A、FLAC 等格式。上传后会保留任务副本。</small>
        {uploadFiles.map((file, index) => <span key={index}>{file.name} · {(file.size / 1024 ** 2).toFixed(2)} MB</span>)}
      </label>}
      <div className="form-section-heading"><span>02</span><div><h2>选择处理方式</h2><p>按内容需要配置转写、画面识别与文本整理</p></div></div>
      <fieldset>
        <legend>文本获取方式</legend>
        <div className="segmented">
          <button
            className={textSource === "ASR" ? "selected" : ""}
            onClick={() => setTextSource("ASR")}
          >
            ASR 语音识别
          </button>
          <button
            className={textSource === "SUBTITLE" ? "selected" : ""}
            onClick={() => {
              setTextSource("SUBTITLE");
              if (sourceType !== "URL") {
                setSourceType("URL");
                setSources("");
              }
            }}
          >
            优先抓取字幕（缺失时 ASR）
          </button>
        </div>
        {textSource === "SUBTITLE" && <div className="notice">
          先检查 yt-dlp 与 B 站播放器字幕接口，优先人工中文字幕，再回退到自动字幕。
          启用下方选项后会继续 OCR 视频下方的硬字幕；仍未发现字幕时，自动调用当前处理方案中的 ASR 模型识别音频。
        </div>}
        {textSource === "SUBTITLE" && <label className="check">
          <input type="checkbox" checked={embeddedCaptionFallback} onChange={event => setEmbeddedCaptionFallback(event.target.checked)} />
          没有平台字幕时识别视频下方内嵌字幕（需要 OCR 模型）
        </label>}
      </fieldset>
      <div className="form-grid">
        <label>处理方案
          <select
            value={profileId}
            onChange={event => {
              const value = event.target.value;
              setProfileId(value);
              const profile = profiles.find(item => String(item.id) === value);
              if (profile) {
                setOcrEnabled(profile.ocr_enabled);
                setTextEditEnabled(Boolean(profile.text_model_id));
              }
            }}
          >
            <option value="">默认 / 上次使用</option>
            {profiles.filter(item => item.enabled).map(item => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>{textSource === "SUBTITLE" ? "ASR 回退 Gateway" : "ASR Gateway"}
          <select
            value={asrGatewayId}
            onChange={event => {
              setAsrGatewayId(event.target.value);
              setAsrGatewayModelId("");
            }}
          >
            <option value="">使用处理方案中的 Gateway</option>
            {asrGateways.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label>{textSource === "SUBTITLE" ? "ASR 回退模型" : "ASR 模型"}
          <select
            value={asrGatewayModelId}
            disabled={!asrGatewayId}
            onChange={event => setAsrGatewayModelId(event.target.value)}
          >
            <option value="">使用处理方案中的模型</option>
            {asrModels.map(item => (
              <option key={item.id} value={item.id}>{item.display_name}</option>
            ))}
          </select>
        </label>
      </div>
      {selectedProfile && <div className="notice">
        <strong>当前方案模型：</strong>{" "}
        ASR {selectedProfile.asr_selection?.display_name ?? "未设置"}；
        OCR {selectedProfile.ocr_enabled
          ? selectedProfile.ocr_selection?.display_name ?? "未设置"
          : "关闭"}；
        文本整理 {selectedProfile.text_selection?.display_name ?? "未启用"}。
      </div>}

      <label className="check">
        <input
          type="checkbox"
          checked={ocrEnabled}
          onChange={event => setOcrEnabled(event.target.checked)}
        />
        启用真实视频抽帧、OCR 与 ASR/OCR 时间线融合
      </label>
      {ocrRequired && <>
        <div className="form-grid">
          <label>OCR Gateway
            <select
              value={ocrGatewayId}
              onChange={event => {
                setOcrGatewayId(event.target.value);
                setOcrGatewayModelId("");
              }}
            >
              <option value="">使用处理方案中的 Gateway</option>
              {ocrGateways.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          </label>
          <label>OCR 模型
            <select
              value={ocrGatewayModelId}
              disabled={!ocrGatewayId}
              onChange={event => setOcrGatewayModelId(event.target.value)}
            >
              <option value="">使用处理方案中的模型</option>
              {ocrModels.map(item => (
                <option key={item.id} value={item.id}>{item.display_name}</option>
              ))}
            </select>
          </label>
        </div>
        {ocrEnabled && <details>
          <summary>OCR 抽帧与融合参数</summary>
          <div className="form-grid">
            <label>抽帧策略
              <select
                value={frameStrategy}
                onChange={event => setFrameStrategy(event.target.value as typeof frameStrategy)}
              >
                <option value="hybrid">固定间隔 + 画面变化</option>
                <option value="fixed_interval">固定间隔</option>
                <option value="scene_change">画面变化</option>
              </select>
            </label>
            <label>固定间隔（秒）
              <input
                type="number"
                min={1}
                max={3600}
                value={frameInterval}
                onChange={event => setFrameInterval(Number(event.target.value))}
              />
            </label>
            <label>场景阈值
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={sceneThreshold}
                onChange={event => setSceneThreshold(Number(event.target.value))}
              />
            </label>
            <label>最大候选帧
              <input
                type="number"
                min={1}
                max={10000}
                value={maxCandidateFrames}
                onChange={event => setMaxCandidateFrames(Number(event.target.value))}
              />
            </label>
            <label>ASR 关联窗口（秒）
              <input
                type="number"
                min={0}
                max={300}
                value={fusionWindow}
                onChange={event => setFusionWindow(Number(event.target.value))}
              />
            </label>
          </div>
        </details>}
      </>}

      <label className="check">
        <input
          type="checkbox"
          checked={textEditEnabled}
          onChange={event => setTextEditEnabled(event.target.checked)}
        />
        启用 LLM 忠实轻度整理（响应不合规则自动回退原文）
      </label>
      {textEditEnabled && <label>文本模型
        <select value={textModelId} onChange={event => setTextModelId(event.target.value)}>
          <option value="">使用处理方案中的模型</option>
          {textModels.map(item => <option key={item.id} value={item.id}>{item.display_name || item.name}</option>)}
        </select>
      </label>}

      <div className="form-section-heading"><span>03</span><div><h2>保存你的文字材料</h2><p>同时导出 Markdown 和 Word，随时阅读与编辑</p></div></div>
      <fieldset>
        <legend>任务导出位置</legend>
        <div className="form-grid">
          <label>成品目录（绝对路径）
            <input
              value={exportDirectory}
              onChange={event => setExportDirectory(event.target.value)}
              placeholder="D:\\Notes\\Video2Doc"
            />
          </label>
          <button onClick={() => void pickOutput()} disabled={pickingOutput}>
            {pickingOutput ? "等待选择…" : "选择导出文件夹…"}
          </button>
        </div>
        {!exportDirectory && <small>
          未指定时使用应用设置中的默认导出位置。
        </small>}
        <small>同一目录保留同名的 Markdown 和 Word，默认不写入媒体时间戳；Word 内嵌相关画面。</small>
      </fieldset>

      <div className="notice">
        不启用文本模型时，转写后会等待人工校对。可将初稿交给网页大模型修改后填回，也可直接使用初稿完成。
        LLM、OCR、ASR 连接分别在左侧对应页面配置。
      </div>
      {message && <p className="form-message">{message}</p>}
      <div className="actions">
        <button className="primary" disabled={busy || initializing} onClick={() => void submit()}>
          <Icon name="plus" width="17" height="17" />
          {busy ? (editTaskId ? "正在保存…" : (sourceType === "UPLOAD" ? "正在上传音频…" : "正在创建…")) : (editTaskId ? "保存队列修改" : "加入处理队列")}
        </button>
        <button disabled={busy || (!sources && !uploadFiles.length)} onClick={() => { setSources(""); setUploadFiles([]); setUploadKey(value => value + 1); }}>清空输入</button>
        {editTaskId && <button onClick={() => { window.location.hash = "/queue"; }}>取消编辑</button>}
      </div>
    </section>
  </>;
}
