/**
 * @startingPoint section="Components" subtitle="Text input, select, checkbox, radio, switch" viewport="700x360"
 */
export interface InputProps {
  label?: string;
  hint?: string;
  /** Replaces hint, turns border red */
  error?: string;
  /** Leading Lucide icon */
  icon?: string;
  size?: 'sm' | 'md' | 'lg';
  type?: string;
  value?: string;
  defaultValue?: string;
  placeholder?: string;
  disabled?: boolean;
  onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
  style?: React.CSSProperties;
}
export declare function Input(props: InputProps): JSX.Element;