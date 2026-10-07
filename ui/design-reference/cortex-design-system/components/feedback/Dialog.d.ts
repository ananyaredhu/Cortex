export interface DialogProps {
  open?: boolean;
  onClose?: () => void;
  title: string;
  description?: string;
  children?: React.ReactNode;
  /** Buttons, right-aligned. Primary last. */
  footer?: React.ReactNode;
  width?: number;
  /** Render without the fixed overlay (for docs/cards) */
  inline?: boolean;
}
export declare function Dialog(props: DialogProps): JSX.Element | null;