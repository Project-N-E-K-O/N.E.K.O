import "./Button.css";

type ButtonProps = {
  label: string;
  onClick?: () => void;
};

export function Button({ label, onClick }: ButtonProps) {
  return (
    <button className="btn" type="button" onClick={onClick}>
      {label}
    </button>
  );
}

