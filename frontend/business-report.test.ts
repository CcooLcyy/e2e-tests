import assert from 'node:assert/strict';
import { test } from 'node:test';
import { businessScreenshots, resultSummary } from './business-report.ts';

test('默认截图说明只使用业务视图，不把异常和定位细节带进报告', () => {
  const source = {name:'failure.png',path:'failure.png',data_url:'image',step_id:91,
    description:'RuntimeError: driver payload',actual:'Traceback private payload',error:'stack',
    annotation_error:'WebDriverException',annotations:[{label:'internal selector',reason:'RuntimeError'}]};
  const view = {title:'错误密码登录',goal:'错误密码应有明确提示',result:'失败',summary:'提示不符合预期',
    checks:[{step_id:91,name:'认证错误提示',status:'失败',expected:'明确提示密码错误',actual:'显示通用登录失败'}],
    key_screenshots:['failure.png']};
  const shots = businessScreenshots({business:view,screenshots:[source]});
  assert.equal(shots.length,1);
  assert.equal(shots[0].description,'认证错误提示');
  assert.equal(shots[0].actual,'显示通用登录失败');
  assert.equal(shots[0].business_result,'失败');
  assert.ok(!JSON.stringify(shots).includes('payload'));
  assert.ok(!JSON.stringify(shots).includes('RuntimeError'));
  assert.equal(source.actual,'Traceback private payload');
});

test('关键图片由业务视图选择，其余逐步证据不默认展开', () => {
  const business = {title:'登录',goal:'登录',result:'通过',summary:'符合要求',checks:[],key_screenshots:['key.png']};
  assert.deepEqual(businessScreenshots({business,screenshots:[
    {name:'internal.png',path:'internal.png',data_url:'internal'},
    {name:'key.png',path:'key.png',data_url:'key'},
  ]}).map(shot=>shot.name),['key.png']);
  assert.deepEqual(businessScreenshots({screenshots:[{name:'old.png',path:'old.png',data_url:'old'}]}),[]);
});

test('执行异常及未完成状态只提供业务提示，不依赖原始异常字符串', () => {
  assert.match(resultSummary('failed'),/未通过/);
  assert.match(resultSummary('cancelled'),/停止/);
  assert.match(resultSummary('running'),/执行/);
});

test('旧截图没有步骤编号时不能借用另一检查的说明', () => {
  const view = {title:'历史用例',goal:'历史现场',result:'失败',summary:'未完成',
    checks:[{step_id:null,name:'不相关检查',status:'失败',expected:'无关预期',actual:'无关实际'}],key_screenshots:['old.png']};
  const shots = businessScreenshots({business:view,screenshots:[{name:'old.png',path:'old.png',data_url:'original',step_id:null,annotated_data_url:'old-technical-image'}]});
  assert.equal(shots[0].description,'历史现场');
  assert.equal(shots[0].annotated_data_url,'');
  assert.equal(shots[0].data_url,'original');
});

test('只有新版业务标注副本进入默认报告，旧副本仍留在原始证据中', () => {
  const view = {title:'登录',goal:'登录现场',result:'通过',summary:'通过',checks:[],key_screenshots:['key.png']};
  const shot = {name:'key.png',path:'key.png',data_url:'original',annotated_data_url:'annotated',annotation_format:'business-v1'};
  assert.equal(businessScreenshots({business:view,screenshots:[shot]})[0].annotated_data_url,'annotated');
});
