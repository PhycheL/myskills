/**
 * 项目文档 UML 图统一渲染配置：黑白印刷风格，渲染完成后在 body 上标记 data-rendered，
 * 供截图脚本判断。每个图源 HTML 只需引入 mermaid.min.js 与本文件。
 */
mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'loose',
  theme: 'base',
  look: 'classic',
  fontFamily: '"PingFang SC","Microsoft YaHei","SimSun",sans-serif',
  themeVariables: {
    fontSize: '15px',
    background: '#ffffff',
    primaryColor: '#ffffff',
    primaryTextColor: '#111111',
    primaryBorderColor: '#333333',
    secondaryColor: '#f5f5f5',
    tertiaryColor: '#fafafa',
    lineColor: '#333333',
    textColor: '#111111',
    clusterBkg: '#fafafa',
    clusterBorder: '#555555',
    actorBkg: '#ffffff',
    actorBorder: '#333333',
    actorLineColor: '#555555',
    signalColor: '#333333',
    signalTextColor: '#111111',
    labelBoxBkgColor: '#ffffff',
    labelBoxBorderColor: '#333333',
    noteBkgColor: '#fffbe6',
    noteBorderColor: '#999999',
    edgeLabelBackground: '#ffffff',
    dropShadow: 'none',
  },
  flowchart: { htmlLabels: true, curve: 'basis', wrappingWidth: 220, useMaxWidth: false },
  sequence: { mirrorActors: false, showSequenceNumbers: true, useMaxWidth: false },
  class: { useMaxWidth: false },
  state: { useMaxWidth: false },
  er: { useMaxWidth: false },
  usecase: { useMaxWidth: false },
});

mermaid
  .run({ querySelector: '.mermaid' })
  .then(function () {
    document.body.dataset.rendered = '1';
  })
  .catch(function (error) {
    document.body.dataset.rendered = 'error';
    document.body.dataset.error = String((error && error.message) || error);
  });
