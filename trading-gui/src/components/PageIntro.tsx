// One-line "what is this page for" banner — same visual grammar as the Lab's
// paper-lab notice, rendered at the top of every page so no view needs
// prior knowledge to read.
export default function PageIntro(props: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-blue/40 bg-blue/10 px-3 py-2 text-xs text-blue">
      {props.icon} <strong>{props.title}</strong> {props.children}
    </div>
  );
}
