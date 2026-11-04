import { invoke } from '@tauri-apps/api/core';
import type { BenchEvent } from './state';
export interface Environment { name: string; app_path: string; driver_path: string; mode: 'fresh' | 'workspace'; workspace_path: string; baseline_path: string; timeout: number; close_app: boolean; slavesim_url: string }
export interface CaseInfo { id: string; name: string; description: string; group?: string }
export interface History { id: string; status: string; environment_name: string; created_at: string; case_count: number; passed: number; failed: number; run_dir: string }
export interface Shot { name: string; path: string; data_url: string; description?: string; expected?: unknown; actual?: unknown; status?: string; capture_status?: string; error?: string; time?: string }
export interface Detail { report: Record<string, unknown>; screenshots: Shot[] }
export interface Bootstrap { root: string; environment: Environment; cases: CaseInfo[]; history: History[] }
export interface Poll { active: boolean; run_dir: string | null; events: BenchEvent[] }
export const desktop = '__TAURI_INTERNALS__' in window;
export function call<T>(method: string, payload: unknown = {}): Promise<T> { return invoke<T>('bridge_call', {method, payload}); }
export function choose(kind: 'exe' | 'directory' | 'environment'): Promise<string | null> { return invoke('choose_path', {kind}); }
export function openArtifact(path: string): Promise<void> { return invoke('open_artifact', {path}); }
