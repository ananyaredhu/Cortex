import React from 'react';
import { IconButton } from '../core/IconButton.jsx';
export function Dialog({ open = true, onClose, title, description, children, footer, width = 480, inline }) {
  if (!open) return null;
  const panel = (
    <div role="dialog" aria-modal="true" onClick={e => e.stopPropagation()} style={{ width, maxWidth: 'calc(100vw - 32px)', background: 'var(--surface-raised)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-3)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: '20px 20px 0 24px' }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 4 }}>
          <div style={{ font: '700 20px/1.2 var(--font-sans)', letterSpacing: '-0.02em', color: 'var(--text-strong)' }}>{title}</div>
          {description && <div style={{ font: '400 14px/1.45 var(--font-sans)', color: 'var(--text-body)', textWrap: 'pretty' }}>{description}</div>}
        </div>
        {onClose && <IconButton icon="x" label="Close" size="sm" onClick={onClose} />}
      </div>
      {children && <div style={{ padding: '20px 24px 0' }}>{children}</div>}
      {footer && <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, padding: 24 }}>{footer}</div>}
      {!footer && <div style={{ height: 24 }} />}
    </div>
  );
  if (inline) return panel;
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 50, background: 'rgba(20,20,20,.32)', backdropFilter: 'blur(2px)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{panel}</div>
  );
}