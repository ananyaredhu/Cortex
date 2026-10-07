// Preview-only loader: compiles the component sources in-browser for cards and UI kits.
(function(){
  var FILES = ['core/Icon.jsx','core/Button.jsx','core/IconButton.jsx','core/Badge.jsx','core/SourcePill.jsx','core/Avatar.jsx','core/AvatarGroup.jsx','core/Logo.jsx','core/Card.jsx','forms/Input.jsx','forms/Select.jsx','forms/Checkbox.jsx','forms/Radio.jsx','forms/Switch.jsx','navigation/Tabs.jsx','navigation/Menu.jsx','feedback/Dialog.jsx','feedback/Toast.jsx','feedback/Tooltip.jsx'];
  var here = document.currentScript.src.replace(/[^/]*$/, '');
  window.CortexLoad = function(){
    if (window.__cortexP) return window.__cortexP;
    return window.__cortexP = Promise.all(FILES.map(function(f){ return fetch(here + f).then(function(r){ return r.text(); }); })).then(function(srcs){
      var code = srcs.map(function(s){ return s.replace(/^import .*$/gm, '').replace(/^export function/gm, 'function'); }).join('\n');
      var names = []; code.replace(/^function ([A-Z]\w*)/gm, function(_, n){ names.push(n); });
      code = 'window.__C = (function(){' + code + '\nreturn {' + names.join(',') + '};})();';
      new Function('React', Babel.transform(code, { presets: ['react'] }).code)(React);
      return window.Cortex = window.__C;
    });
  };
})();
