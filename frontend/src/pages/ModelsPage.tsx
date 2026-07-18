import { useState } from "react";

import { api } from "../api/client";
import type { Capability, ModelConfig } from "../types";

const capabilities: Capability[] = ["ASR", "OCR", "TEXT_PROCESSING", "VISION"];

export function ModelsPage({ models, refresh }: { models: ModelConfig[]; refresh: () => Promise<void> }) {
  const [name, setName] = useState(""); const [notes, setNotes] = useState(""); const [selected, setSelected] = useState<Capability[]>([]); const [message, setMessage] = useState<string | null>(null); const [editingId, setEditingId] = useState<number | null>(null);
  const toggle = (value: Capability) => setSelected(current => current.includes(value) ? current.filter(item => item !== value) : [...current, value]);
  const save = async () => { try { if (editingId) { await api.updateModel(editingId, { name, notes, capabilities: selected }); } else { await api.createModel({ name, notes, capabilities: selected }); } setName(""); setNotes(""); setSelected([]); setEditingId(null); setMessage(null); await refresh(); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "保存失败"); } };
  const edit = (model: ModelConfig) => { setEditingId(model.id); setName(model.name); setNotes(model.notes ?? ""); setSelected(model.capabilities); setMessage(null); };
  const remove = async (id: number) => { try { await api.deleteModel(id); await refresh(); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "删除失败"); } };
  const toggleEnabled = async (model: ModelConfig) => { try { await api.updateModel(model.id, { enabled: !model.enabled }); await refresh(); } catch (reason) { setMessage(reason instanceof Error ? reason.message : "更新失败"); } };
  return <><header className="page-header"><div><p className="eyebrow">固定能力标签</p><h1>模型管理</h1><p>模型只保存 OneAPI 模型 ID、能力、备注与启用状态；本轮不发起模型调用。</p></div></header>
    <section className="split"><div className="panel form-panel"><h2>{editingId ? "编辑模型" : "添加模型"}</h2><label>模型名称<input value={name} onChange={event => setName(event.target.value)} placeholder="whisper-large-v3"/></label><fieldset><legend>能力标签</legend><div className="tag-options">{capabilities.map(value => <label className="check" key={value}><input type="checkbox" checked={selected.includes(value)} onChange={() => toggle(value)}/>{value}</label>)}</div></fieldset><label>备注<textarea rows={3} value={notes} onChange={event => setNotes(event.target.value)}/></label>{message && <p className="form-message error-text">{message}</p>}<div className="actions"><button className="primary" disabled={!name.trim() || !selected.length} onClick={() => void save()}>{editingId ? "保存修改" : "添加模型"}</button>{editingId && <button onClick={() => { setEditingId(null); setName(""); setNotes(""); setSelected([]); }}>取消编辑</button>}</div></div>
    <div className="panel"><h2>已配置模型</h2>{models.length === 0 ? <div className="state-card">尚未添加模型。</div> : <div className="card-list">{models.map(model => <article className={!model.enabled ? "disabled-card" : ""} key={model.id}><div><strong>{model.name}</strong><small>{model.notes || "无备注"}</small><div className="tags">{model.capabilities.map(tag => <span key={tag}>{tag}</span>)}</div></div><div className="actions"><button onClick={() => edit(model)}>编辑</button><button onClick={() => void toggleEnabled(model)}>{model.enabled ? "停用" : "启用"}</button><button onClick={() => void remove(model.id)}>删除</button></div></article>)}</div>}</div></section></>;
}
