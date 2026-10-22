import Link from "next/link";

export const metadata = { title: "Política de Privacidade · FinanceMed AI" };

export default function Privacidade() {
  return (
    <main className="auth" style={{ maxWidth: 720 }}>
      <h1>Política de Privacidade</h1>
      <p role="note" className="card"><strong>Minuta para validação.</strong> Controlador, encarregado (DPO) e base legal por finalidade serão definidos na revisão jurídica.</p>
      <h2>O que coletamos</h2>
      <p>Cadastro (nome, e-mail), dados financeiros e de agenda que você informa (rendas, despesas, metas, escalas) e registros de uso e segurança. <strong>Não coletamos CPF, diagnóstico, prontuário ou nome de pacientes</strong>; evite digitá-los em campos livres — o histórico do Consultor IA remove e-mail, CPF/CNPJ e telefone, mas não é garantia.</p>
      <h2>Para quê</h2>
      <p>Calcular metas, cenários, relatórios e avisos que você pediu; manter a segurança da conta; cobrar a assinatura. Seus dados não são vendidos.</p>
      <h2>Seus direitos</h2>
      <ul>
        <li><strong>Acesso e portabilidade:</strong> exporte seus dados em Configurações e em Planos &amp; Cobrança.</li>
        <li><strong>Exclusão:</strong> exclua a conta em Configurações (exige senha, confirmação do e-mail e 2FA, se ativo). Apagamos os dados da organização; <strong>faturas e registros de auditoria são retidos sem dados pessoais</strong> por obrigação fiscal e de segurança.</li>
        <li><strong>Retenção:</strong> sessões vencidas, avisos lidos há mais de 180 dias, conversas excluídas do consultor e relatórios excluídos há mais de 30 dias são removidos periodicamente.</li>
      </ul>
      <h2>Segurança</h2>
      <p>Conexão criptografada, senhas com hash forte, 2FA opcional, isolamento entre clínicas, permissões por papel, bloqueio contra tentativas repetidas e registro de auditoria sem valores financeiros.</p>
      <h2>Inteligência artificial</h2>
      <p>O Consultor IA atual não envia seus dados a terceiros. Se um provedor externo for adotado, avisaremos antes e avaliaremos contrato e transferência internacional.</p>
      <p><Link href="/login">Voltar</Link></p>
    </main>
  );
}
