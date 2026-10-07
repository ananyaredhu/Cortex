export interface ToastProps {
  message: string;
  icon?: string;
  tone?: 'default' | 'success' | 'danger';
  action?: string;
  onAction?: () => void;
  onClose?: () => void;
}
export declare function Toast(props: ToastProps): JSX.Element;