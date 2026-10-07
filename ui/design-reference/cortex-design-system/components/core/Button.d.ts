/**
 * @startingPoint section="Components" subtitle="Primary, accent, secondary, ghost buttons" viewport="700x300"
 */
export interface ButtonProps {
  /** primary = ink (default action); accent = orange (one per view, "connect/ask"); secondary; ghost; danger */
  variant?: 'primary' | 'accent' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
  /** Lucide icon name, leading */
  icon?: string;
  iconRight?: string;
  fullWidth?: boolean;
  disabled?: boolean;
  type?: 'button' | 'submit';
  onClick?: (e: React.MouseEvent) => void;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}
export declare function Button(props: ButtonProps): JSX.Element;