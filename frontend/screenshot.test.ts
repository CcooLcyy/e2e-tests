import assert from 'node:assert/strict';
import { test } from 'node:test';
import { screenshotSource, hasAnnotation } from './screenshot.ts';

const shot = {name:'step.png',path:'step.png',data_url:'original',capture_status:'saved',annotation_status:'saved',annotated_data_url:'annotated'};

test('默认展示标注副本，可切回原图', () => {
  assert.equal(screenshotSource(shot), 'annotated');
  assert.equal(screenshotSource(shot, true), 'original');
  assert.equal(hasAnnotation(shot), true);
});
test('旧记录、标注失败或副本丢失均回退原图', () => {
  assert.equal(screenshotSource({name:'old',path:'old',data_url:'original'}), 'original');
  assert.equal(screenshotSource({...shot,annotation_status:'failed'}), 'original');
  assert.equal(screenshotSource({...shot,annotated_data_url:''}), 'original');
});
test('缺失原始证据不能用副本冒充截图成功', () => {
  assert.equal(screenshotSource({...shot,capture_status:'missing'}), '');
  assert.equal(hasAnnotation({...shot,data_url:''}), false);
});
