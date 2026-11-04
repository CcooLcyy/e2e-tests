import type { Shot } from './api';

export function hasAnnotation(shot: Shot): boolean {
  return shot.capture_status !== 'missing' && Boolean(shot.data_url)
    && shot.annotation_status === 'saved' && Boolean(shot.annotated_data_url);
}

export function screenshotSource(shot: Shot, original = false): string {
  if (shot.capture_status === 'missing' || !shot.data_url) return '';
  return !original && hasAnnotation(shot) ? shot.annotated_data_url! : shot.data_url;
}
