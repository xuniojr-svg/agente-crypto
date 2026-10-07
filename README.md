# Agente Crypto: Fase 1 (simulação)

Nesta fase não há dinheiro real, conta em exchange nem chave de API. O sistema roda inteiro no seu computador, com uma exchange simulada que cobra as taxas do Mercado Bitcoin: 0,70% para ordens que executam na hora (taker) e 0,30% para ordens limitadas que esperam no livro (maker).

## Como rodar

Você precisa do Python 3.9 ou mais novo (o que já vem no macOS serve).

```bash
cd codigo
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                  # 47 testes
agente-crypto backtest --sintetico      # roda sem internet, com preços aleatórios
agente-crypto baixar --dias 180         # baixa candles reais de BTC/BRL do Mercado Bitcoin (API pública)
agente-crypto backtest                  # roda a estratégia de config/strategy.yaml sobre os dados
agente-crypto estudo                    # compara 32 configurações (16 conservadoras, 16 de swing) com treino e validação
caffeinate -i agente-crypto ao-vivo     # robô ao vivo SIMULADO; caffeinate impede o Mac de dormir
agente-crypto situacao                  # mostra patrimônio simulado, posição e execuções
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
| `config/strategy.yaml` | Tipo de estratégia (`tendencia` ou `swing`), parâmetros, timeframe, tipo de ordem (maker/taker), taxas e capital simulado. |
| `src/agente_crypto/estudo.py` | Testa várias configurações: escolhe a melhor nos primeiros 60% dos dados e confere nos últimos 40%. |
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

## As duas estratégias

- **Conservadora (`tendencia`):** fica comprada enquanto a média curta estiver acima da longa, em candles de 4h ou de 1 dia. Opera pouco.
- **Trade ativo (`swing`):** compra quando o preço cai X% abaixo da média recente e vende quando ele volta à média. Tem stop de 5% abaixo do custo, que usa ordem agressiva para garantir a saída. Roda em candles de 1h ou 4h e opera muito mais.

O `estudo` testa as duas famílias e mostra, lado a lado, a melhor de cada uma (escolhida no treino) e como ela se saiu na validação, já descontando as taxas.

## Robô ao vivo (simulado)

`agente-crypto ao-vivo` lê o preço real do Mercado Bitcoin a cada minuto (API pública, sem conta) e roda a estratégia de `config/strategy.yaml` a cada candle fechado (com candles de 1 dia, uma decisão por dia, logo depois das 21h de Brasília). As ordens vão para a exchange simulada: nenhuma ordem real é enviada.

- Tudo fica em `dados/ao_vivo/ao_vivo.sqlite`, com a mesma auditoria do backtest.
- Pode parar (Ctrl+C) e ligar de novo: a carteira é refeita a partir das execuções gravadas.
- `agente-crypto kill "motivo"` para o robô; apagar `dados/ao_vivo/KILL` libera de novo.
- Se a internet cair, ele registra o erro e tenta de novo no minuto seguinte.

## Como ler o backtest

- **Comparação justa:** o robô aplica só o valor de uma ordem (R$500), então ele é comparado com comprar esses mesmos R$500 e segurar, e não com aplicar o patrimônio todo.
- **Ordem maker:** a ordem fica no livro e só executa se o candle seguinte passar do preço limite. Encostar no preço não basta, porque na vida real existe fila. Se a ordem não executar, ela é cancelada e a estratégia tenta de novo no candle seguinte.
- **Estudo:** uma configuração só conta como candidata se mantiver uma vantagem de pelo menos 10% do valor da ordem, com 10 ou mais execuções, nos dados de validação que ela não viu. Com dados aleatórios (`--sintetico`), o resultado esperado é "não há evidência", e é isso que acontece.

## Próximos passos da Fase 1

1. Rodar o estudo com mais história (2 a 3 anos) para ter mais operações na validação.
2. ~~Modo paper ao vivo~~ (pronto: `agente-crypto ao-vivo`). Deixar rodando algumas semanas.
3. Painel simples com patrimônio, posições e log de decisões.
4. Opcional: um LLM como analista de contexto, que só gera texto e sugestões e nunca uma ordem.
5. Adaptador de testnet (OKX Demo ou Binance Testnet) para testar a integração real com uma API.
