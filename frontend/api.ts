import { invoke } from '@tauri-apps/api/core';
import type { BenchEvent } from './state';
export interface ModbusStation { station_id: number; address: number }
export interface ModbusBasic { device_name?: string; bus?: string; serial_device?: string; stations?: ModbusStation[]; dedicated_test_resources?: boolean; data_bits?: number; stop_bits?: number; parity?: '无校验' | '奇校验' | '偶校验' }
export interface ModbusHuawei { serial_devices?: string[]; dedicated_simulator?: boolean }
export interface ModbusEnvironmentFields { modbus_manager_address?: string; modbus_baud_rate?: number; modbus_poll_interval_ms?: number; modbus_acquisition_timeout?: number; modbus_basic?: ModbusBasic; modbus_huawei?: ModbusHuawei }
export interface Environment extends ModbusEnvironmentFields { name: string; app_path: string; driver_path: string; mode: 'fresh' | 'workspace'; workspace_path: string; baseline_path: string; timeout: number; close_app: boolean; slavesim_url: string; annotation_enabled: boolean; master104_enabled: boolean; master104_path: string; master104_backend: 'win32' | 'uia'; master104_timeout: number }
export interface Master104Check { checking: boolean; message?: string; snapshot?: Record<string, unknown> }
export interface CaseInfo { id: string; name: string; description: string; group?: string }
export interface History { id: string; status: string; environment_name: string; created_at: string; case_count: number; passed: number; failed: number; cancelled?: number; skipped?: number; run_dir: string }
export interface Shot { name: string; path: string; data_url: string; description?: string; expected?: unknown; actual?: unknown; status?: string; capture_status?: string; error?: string; time?: string; annotated_name?: string; annotated_path?: string; annotated_data_url?: string; annotation_status?: string; annotation_format?: string; annotation_error?: string; annotations?: Array<{label: string; reason?: string}>; step_id?: number | string | null; business_result?: string }
export interface BusinessReport { title: string; goal: string; result: string; summary: string; checks: Array<{step_id?: number | string | null; name: string; status: string; expected: string; actual: string}>; key_screenshots: string[] }
export interface Detail { report: Record<string, unknown>; screenshots: Shot[]; business?: BusinessReport; diagnostics_path?: string; business_report_path?: string }
export interface Bootstrap { root: string; environment: Environment; cases: CaseInfo[]; history: History[] }
export interface Poll { active: boolean; run_dir: string | null; events: BenchEvent[] }
export const desktop = '__TAURI_INTERNALS__' in window;
export function call<T>(method: string, payload: unknown = {}): Promise<T> { return invoke<T>('bridge_call', {method, payload}); }
export function choose(kind: 'exe' | 'directory' | 'environment'): Promise<string | null> { return invoke('choose_path', {kind}); }
export function openArtifact(path: string): Promise<void> { return invoke('open_artifact', {path}); }
