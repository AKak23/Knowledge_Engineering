const { chromium }=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'}), page=await browser.newPage({viewport:{width:1512,height:982}}),errors=[];
 page.on('pageerror',e=>errors.push(e.stack));
 await page.goto(process.env.PREVIEW_URL || 'http://127.0.0.1:8765/frontend/workspace-preview/index.html'); await page.waitForTimeout(1800);
 await page.locator('[name=ontologyName]').fill('新建业务本体');await page.locator('.ws-blank-button').click();
 assert.equal(await page.locator('#appRoot').evaluate(e=>getComputedStyle(e).visibility),'visible');
 await page.getByRole('button',{name:'我的本体管理'}).click();assert.equal(await page.locator('.ws-project').count(),2);
 await page.locator('[data-type="draft"]').click();assert.equal(await page.locator('#globalOntologyName').textContent(),'新建业务本体');
 await page.locator('.ws-sidebar [data-page="create"]').click();await page.locator('#wsSample').click();await page.waitForTimeout(350);
 assert.equal(await page.locator('#startBuildBtn').isEnabled(),true);
 await page.locator('.ws-sidebar [data-page="files"]').click();assert.equal(await page.locator('.ws-file-row').count(),1);
 await page.reload();await page.waitForTimeout(800);await page.locator('#wsPickLibrary').click();await page.locator('.ws-check input').check();await page.locator('#wsApply').click();assert.equal(await page.locator('#startBuildBtn').isEnabled(),true);assert.equal(await page.locator('#wsPickLibrary').count(),1);
 // Actual failure path: static service cannot accept a build. Preserve form and retry action.
 await page.locator('#startBuildBtn').click();await page.waitForTimeout(500);assert.equal(await page.locator('#workbenchModal').isVisible(),true);assert.equal(await page.locator('#startBuildBtn').isEnabled(),true);
 // Exercise asynchronous real frontend lifecycle using deterministic test HTTP responses.
 const mock=await page.evaluate(()=>window.LOM_MOCK_ONTOLOGY);
 await page.route('**/ontology/jobs',route=>route.request().method()==='POST'?route.fulfill({json:{jobId:'preview-test-job'}}):route.fulfill({json:{jobs:[]}}));
 let polls=0;
 await page.route('**/ontology/jobs/preview-test-job/status',route=>{polls++;route.fulfill({json:{status:polls===1?'running':'completed',stage:polls===1?'概念抽取':'构建完成',stageHint:'回归测试状态',progress:{overallPercent:polls===1?45:100,completed:1,total:1},stageHistory:[{stage:'文档解析',hint:'1 份文件已读取'},{stage:'概念抽取',hint:'合并对象与关系'}]}})});
 await page.route('**/ontology/jobs/preview-test-job/graph?*',route=>route.fulfill({json:mock.graphs[route.request().url().includes('view=instance')?'instance':'schema']}));
 await page.locator('#startBuildBtn').click();await page.waitForTimeout(500);assert.equal(await page.locator('.ws-build-active').count(),1);assert.equal(await page.locator('.ws-focused-form').count(),1);assert.equal(await page.locator('.ws-source-card').isVisible(),true);assert.equal(await page.locator('.ws-focused-alternatives').isVisible(),true);await page.screenshot({path:'/tmp/knova-building.png'});
 await page.waitForTimeout(2500);assert.equal(await page.locator('#workbenchModal').isVisible(),false);assert.equal(await page.locator('.ontologyNode').count(),15);
 await page.locator('.ws-sidebar [data-page="projects"]').click();await page.locator('[data-type="demo"]').click();await page.waitForTimeout(500);
 const w=await page.locator('.left').evaluate(e=>e.clientWidth);await page.locator('#chatPanelToggle').click();await page.waitForTimeout(400);assert.ok(await page.locator('.left').evaluate(e=>e.clientWidth)>w);await page.locator('#chatPanelToggle').click();
 await page.locator('#viewInstance').click();await page.waitForTimeout(650);assert.equal(await page.locator('.instance2dNode').count(),12);
 await page.locator('#viewDimension3d').click();await page.waitForTimeout(500);await page.locator('#viewSchema').click();await page.waitForTimeout(500);await page.locator('#viewDimension2d').click();
 await page.locator('.ws-sidebar [data-page="sessions"]').click();assert.ok(await page.locator('.ws-session').count()>0);
 await page.locator('.ws-sidebar [data-page="projects"]').click();await page.screenshot({path:'/tmp/knova-projects.png'});
 await page.locator('.ws-sidebar [data-page="create"]').click();await page.screenshot({path:'/tmp/knova-create.png'});
 assert.equal(await page.evaluate(()=>localStorage.getItem('lomOntologyWorkbenchWorkingCopyV1')),null);
 await page.setViewportSize({width:1280,height:800});await page.screenshot({path:'/tmp/knova-create-1280.png'});
 assert.deepEqual(errors,[]);console.log('PASS: blank create/reopen, persistent file library, failure retry, staged build completion, 15 canvas nodes, 12 instances, four views, chat collapse, recent sessions, original storage isolation; no page errors.');await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
