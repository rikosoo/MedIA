import Link from "next/link";

export const metadata = { title: "Termos de Uso · FinanceMed AI" };

export default function Termos() {
  return (
    <main className="auth" style={{ maxWidth: 720 }}>
      <h1>Termos de Uso</h1>
      <p role="note" className="card"><strong>Minuta para validação.</strong> Este texto ainda precisa de revisão jurídica antes do lançamento público.</p>
      <h2>O que o FinanceMed AI é</h2>
      <p>Uma ferramenta de planejamento financeiro e de agenda para médicos. Ela calcula cenários a partir dos dados que você informa. Não é consultoria contábil, tributária, jurídica ou médica.</p>
      <h2>Limitações importantes</h2>
      <ul>
        <li><strong>Tributos:</strong> são estimativas com as alíquotas que você informa. Confirme com seu contador antes de decidir ou declarar.</li>
        <li><strong>Projeções e previsões:</strong> são hipóteses, não promessas. O app não garante aumento de faturamento.</li>
        <li><strong>Consultor IA:</strong> explica números calculados pelo app; não faz contas oficiais nem altera seus dados. Confira os dados exibidos em “Ver cálculo”.</li>
        <li><strong>Captação:</strong> sugestões de captação devem respeitar as regras de publicidade médica do CFM vigentes; a responsabilidade é do profissional.</li>
        <li><strong>Planejado não é realizado:</strong> valores planejados nunca contam como receita recebida.</li>
      </ul>
      <h2>Sua conta e seus dados</h2>
      <p>Você pode exportar seus dados e cancelar a assinatura a qualquer momento (veja Planos &amp; Cobrança e Configurações). Detalhes em <Link href="/privacidade">Política de Privacidade</Link>.</p>
      <h2>Pagamentos</h2>
      <p>Cobrança, planos e cancelamento seguem o descrito em Planos &amp; Cobrança. Neste ambiente de testes a cobrança é simulada.</p>
      <p><Link href="/login">Voltar</Link></p>
    </main>
  );
}
