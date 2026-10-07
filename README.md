# Agente Crypto: Fase 1 (simulação)

Nesta fase não há dinheiro real, conta em exchange nem chave de API. O sistema roda inteiro no seu computador, com uma exchange simulada que cobra as taxas do Mercado Bitcoin (0,70% taker) e aplica slippage.

## Como rodar

Você precisa do Python 3.11 ou mais novo.

```bash
cd codigo
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                  # 31 testes
agente-crypto backtest --sintetico      # roda sem internet, com preços aleatórios
agente-crypto baixar --dias 180         # baixa candles reais de BTC/BRL do Mercado Bitcoin (API pública)
agente-crypto backtest                  # roda sobre os dados baixados
agente-crypto verificar dados/backtest/backtest.sqlite   # confere se a auditoria foi adulterada
agente-crypto kill "motivo"             # liga o kill switch
```

## Como funciona

```
dados (MB público) -> estratégia -> OrderIntent -> MOTOR DE RISCO -> ApprovedOrder -> executor -> fills
                                                       |                                   |
                                                       +------> ledger (auditoria + fiscal) <+
                                                                         |
                                                       reconciliação: ledger vs. saldo da exchange
```

| Arquivo | Papel |
|---|---|
| `config/risk_limits.yaml` | Limites rígidos. Só mudam editando o arquivo e reiniciando. O hash dele vai gravado em cada decisão. |
| `config/strategy.yaml` | Estratégia de exemplo (médias móveis), as taxas e o capital inicial simulado. |
| `src/agente_crypto/risk.py` | Função pura, sem rede e sem IA. É o único lugar que cria `ApprovedOrder`. |
| `src/agente_crypto/execution/` | O executor só aceita `ApprovedOrder`. A interface **não tem** método de saque nem de transferência. |
| `src/agente_crypto/ledger.py` | Eventos em SQLite com cadeia de hash e livro fiscal, sem UPDATE nem DELETE. |
| `src/agente_crypto/engine.py` | O laço principal. Liga o kill switch sozinho se houver divergência na reconciliação ou 3 erros seguidos da exchange. |

## O que os testes garantem

- **Propriedade do risco:** em milhares de combinações aleatórias, nenhuma ordem aprovada viola limite algum. Também foi testado que remover uma regra do motor faz esse teste falhar.
- Não dá para criar uma ordem aprovada por fora do motor de risco, e o executor recusa intenções cruas.
- Nenhum adaptador de exchange tem método de saque ou transferência.
- Editar ou apagar a auditoria é bloqueado, e uma adulteração feita direto no arquivo é detectada.
- O custo médio e o resultado realizado do livro fiscal batem com um cálculo feito à mão.
- O kill switch para tudo. Divergência de saldo e falhas seguidas da API ligam o kill switch automaticamente.
- Uma "IA" que tenta comprar R$50 mil ou vender a descoberto é barrada.

## Limites atuais (config/risk_limits.yaml)

- R$500 por ordem e R$1.000 por dia em compras
- Perda diária máxima de 2%; depois disso, só vendas
- No máximo 30% do patrimônio em um único ativo e 2 ativos ao mesmo tempo
- No máximo 4 ordens por hora
- Preço limite até 0,5% longe do mercado; spread acima de 1% ou dado mais velho que 5 min bloqueiam tudo
- Só BTC/BRL e ETH/BRL, só spot, sem alavancagem e sem venda a descoberto

## Próximos passos da Fase 1

1. Rodar o backtest com 6 a 12 meses de dados reais do MB e comparar com comprar e segurar.
2. Modo paper ao vivo: o mesmo motor, alimentado pelo ticker público do MB a cada minuto.
3. Painel simples com patrimônio, posições e log de decisões.
4. Opcional: um LLM como analista de contexto, que só gera texto e sugestões e nunca uma ordem.
5. Adaptador de testnet (OKX Demo ou Binance Testnet) para testar a integração real com uma API.
