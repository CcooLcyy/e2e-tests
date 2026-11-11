import { invoke } from '@tauri-apps/api/core';
import type { BenchEvent } from './state';
export interface ModbusEnvironmentFields { modbus_acquisition_timeout?: number }
export interface Environment extends ModbusEnvironmentFields { name: string; app_path: string; driver_path: string; mode: 'fresh' | 'workspace'; workspace_path: string; baseline_path: string; timeout: number; close_app: boolean; slavesim_url: string; annotation_enabled: boolean; master104_enabled: boolean; master104_path: string; master104_timeout: number }
export interface Master104Check { checking: boolean; message?: string; snapshot?: Record<string, unknown> }
export interface FlowStep { id?: string; action: string; expected: string; group?: string }
export interface CaseInfo {
  id: string;
  name: string;
  description: string;
  group?: string;
  opt_in?: boolean;
  flow?: FlowStep[];
  flow_blocked?: string;
  executable_flow?: boolean;
}
export interface History { id: string; status: string; environment_name: string; created_at: string; case_count: number; passed: number; failed: number; cancelled?: number; skipped?: number; run_dir: string }
export interface Shot { name: string; path: string; data_url: string; description?: string; expected?: unknown; actual?: unknown; status?: string; capture_status?: string; error?: string; time?: string; annotated_name?: string; annotated_path?: string; annotated_data_url?: string; annotation_status?: string; annotation_format?: string; annotation_error?: string; annotations?: Array<{label: string; reason?: string}>; step_id?: number | string | null; business_result?: string }
export interface BusinessReport { title: string; goal: string; result: string; summary: string; checks: Array<{step_id?: number | string | null; name: string; status: string; expected: string; actual: string}>; key_screenshots: string[] }
export interface Detail { report: Record<string, unknown>; screenshots: Shot[]; business?: BusinessReport; diagnostics_path?: string; business_report_path?: string; flow?: FlowStep[]; plan_steps?: Record<string, string> }
export type UpdateChannel = 'stable' | 'ci';
export interface CasePackStatus {
  source: 'downloaded' | 'bundled' | 'checkout' | 'legacy';
  version: string;
  source_sha: string;
  sha256: string;
}
export type PreparedCasePack = { status: 'disabled' | 'unavailable' | 'current' } | {
  status: 'ready'; id: string; version: string;
};
export type CasePackActivation = { status: 'deferred' } | {
  status: 'activated'; data: { case_pack: CasePackStatus; cases: CaseInfo[] };
};
export interface Bootstrap { root: string; environment: Environment; cases: CaseInfo[]; history: History[]; installed?: boolean; case_pack?: CasePackStatus; case_pack_warning?: string; private_webview_version?: string }
export interface Poll { active: boolean; run_dir: string | null; events: BenchEvent[] }
export const desktop = '__TAURI_INTERNALS__' in window;
export function call<T>(method: string, payload: unknown = {}): Promise<T> { return invoke<T>('bridge_call', {method, payload}); }
export function choose(kind: 'exe' | 'directory' | 'environment'): Promise<string | null> { return invoke('choose_path', {kind}); }
export function openArtifact(path: string): Promise<void> { return invoke('open_artifact', {path}); }
