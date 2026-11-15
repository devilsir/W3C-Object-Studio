# WC3 Reforged Object Studio v1.0

Release portátil. Título de produção sem DEBUG: **Object Editor completo • MPQ + CASC**. O ícone oficial do projeto é usado na janela e no executável.

### v1.0 R6 — catálogo visual de itens no Inventário e Items Dropped

- **UNIDADES NO MAPA > Inventário** deixa de exigir digitação de rawcode: cada slot ganhou **Escolher…**, **Limpar**, nome e ícone do item.
- O seletor de itens reúne **itens nativos do Warcraft** (ItemData + ItemFunc/ItemStrings via CASC) e **itens custom do próprio mapa** (`war3map.w3t`), respeitando herança do item base.
- Novo catálogo visual com **ícones**, nome, rawcode informativo, nível, classe, tipo de equipamento e origem.
- Filtros por **busca**, **origem (Warcraft/custom)**, **nível**, **classe** e **tipo de equipamento**.
- Painel lateral do seletor mostra **descrição, tooltip/Ubertip, ícone, base e origem** antes de confirmar.
- **Items Dropped > + Item / Editar** usa o mesmo seletor visual; o usuário não precisa mais decorar ou digitar IDs.
- O modo de drop agora usa rótulos no estilo do World Editor: **Nenhum**, **Usar tabela de itens do mapa** e **Usar tabela de itens custom**.
- `war3map.w3t` continua **somente leitura/protegido** nesta função; o Studio apenas usa os dados para apresentar corretamente os itens existentes, sem alterar o Object Data de itens.

### v1.0 R5 — créditos no próprio Studio

- Novo botão **CRÉDITOS / CREDITS** imediatamente ao lado de **ABRIR LOG / OPEN LOG**.
- A janela lista autoria do Object Studio e os projetos/bibliotecas de terceiros usados ou referenciados, com links oficiais.
- Licenças de StormLib, CascLib, war3-model, gl-matrix, tkwry, Pillow e War3AssetsImporter ficam incluídas em `licenses/`.
- Inclui aviso de projeto independente e atribuição de Warcraft III / Blizzard Entertainment.


### v1.0 R4 — mapa realmente editável + terreno reconstruído

- A aba **UNIDADES NO MAPA > MAPA** deixa de usar `war3mapMap.blp` como fonte principal. Muitos mapas substituem esse arquivo por arte de lobby/loading screen; agora o Studio **reconstrói o terreno diretamente de `war3map.w3e`** (ground tiles, água, blight e relevo simplificado) e usa a imagem embutida apenas como fallback.
- Novas ferramentas visuais no mapa: **SELECIONAR**, **MOVER**, **ROTACIONAR**, **+ UNIDADE** e **REMOVER**.
- **Mover**: arraste o marcador. A posição X/Y é gravada no `war3mapUnits.doo`; para placements originais, o Studio sincroniza `SetUnitX/SetUnitY` no bloco JASS marcado, sem regenerar o script.
- **Rotacionar**: arraste a direção do marcador. Facing é sincronizado no DOO + runtime.
- **Remover** agora funciona também para unidades originais do mapa. O registro sai do DOO e o Studio adiciona somente um `RemoveUnit` marcado após `CreateAllUnits`, preservando o JASS original do World Editor. Placements adicionados pelo próprio Studio continuam sendo removidos diretamente do bloco de criação do Studio.
- `Delete` remove a unidade selecionada no mapa; botão direito/Esc cancela a ferramenta ativa.

### v1.0 — importador de assets + adicionar unidades ao mapa

- Novo **IMPORTAR ASSETS…** integrado ao Studio: seleciona arquivos ou uma pasta inteira com MDX/MDL/BLP/TGA/DDS.
- Pode **só importar**, **importar + criar uma unidade custom por modelo**, ou **importar + criar + colocar as unidades no mapa** em grid configurável.
- Modelos mantêm as referências de textura encontradas; BTN e DISBTN soltos vão automaticamente para os caminhos padrão de CommandButtons do Warcraft.
- **UNIDADES NO MAPA** ganhou **+ ADICIONAR UNIDADE**: escolha um tipo já usado/definido no mapa, digite um rawcode nativo, crie uma custom nova ou abra o importador de modelo.
- Placements criados pelo Studio são gravados em `war3mapUnits.doo` e recebem somente um bloco JASS marcado de criação em runtime; o restante do script não é regenerado.
- A remoção pela interface é intencionalmente limitada a placements adicionados pelo próprio Studio, evitando apagar uma unidade original sem também remover o código gerado pelo World Editor.
- Fluxo inspirado no projeto MIT **War3AssetsImporter**, de **Jaccouille**. A licença original está em `licenses/War3AssetsImporter-LICENSE.txt`; a integração continua usando o parser/MPQ/transação nativos do Object Studio.

### v3.10.20 — owners neutros Reforged corrigidos

- Corrige a ordem dos slots reservados: 24 Hostile, 25 Victim, 26 Extra, 27 Passive.
- Runtime sync de owner não grava mais índices neutros hardcoded: usa `PLAYER_NEUTRAL_AGGRESSIVE`, `PLAYER_NEUTRAL_PASSIVE`, `bj_PLAYER_NEUTRAL_VICTIM` e `bj_PLAYER_NEUTRAL_EXTRA`.
- Evita o bug em que selecionar **Neutro Passivo** acabava aplicando `Player(25)`/Neutral Victim e fazia unidades serem tratadas como alvo.

### v3.10.20 — runtime sync seletivo de unidades colocadas

- Corrige o caso em que **Nível do herói** era salvo em `war3mapUnits.doo`, aparecia alterado no World Editor, mas o jogo continuava criando o herói com o nível antigo do `war3map.j`.
- Ao salvar uma instância, o Studio compara a placement atual com o baseline aberto e só sincroniza em JASS os campos de runtime que **realmente mudaram**: **owner/player, facing, HP%, mana%, acquisition range, hero level, atributos e inventário inicial**.
- O `war3map.j` continua protegido. Ele só recebe uma exceção transacional quando um desses campos foi alterado; o patch fica dentro de um bloco marcado do Studio e o restante do script/triggers permanece intacto.
- Saves futuros preservam overrides de runtime já criados pelo Studio e acrescentam somente novos campos modificados, sem apagar correções anteriores.
- Adiciona **SINCRONIZAR RUNTIME** na instância para reparar mapas já salvos por builds antigas: útil quando o DOO já contém, por exemplo, Mountain King nível 5, mas o JASS antigo ainda o cria em nível 1. Esse botão é opt-in; o save normal continua seletivo.
- Quando o herói possui `gg_unit_*`, o patch usa a referência exata. Quando não possui, usa um resolver determinístico por rawcode/posição após `CreateAllUnits()`.
- O campo Object Data `ulev` foi renomeado na interface para **Stats - Nível do tipo da unidade**. Ele não é mais apresentado como “nível inicial do herói”; para nível inicial real use **UNIDADES NO MAPA > Nível do herói**.
- Habilidades modificadas e tabelas de drop continuam preservadas no DOO, mas não recebem compilação JASS automática nesta versão porque o World Editor pode convertê-las em lógica de skill points/order strings e triggers de drop.

### v3.10.18

- Corrige **UNIDADES NO MAPA** para mapas atuais do patch 3.0 que usam `war3mapUnits.doo` **versão 13**.
- O formato v13 adiciona 16 bytes por placement em relação ao layout anterior; o editor preserva esses bytes novos de forma opaca e continua editando owner, nível, stats, inventário, abilities, drops etc.
- Validado com o `war3mapUnits.doo` real do mapa enviado: **74/74 placements**, **72 heróis**, round-trip sem edição **idêntico byte a byte**.
- Inclui teste de alteração de owner e de listas variáveis de inventário/habilidades no v13 sem deslocar os registros seguintes.


### v3.10.17

- UNIDADES NO MAPA > Habilidades: controles visíveis e independentes para **Ativa**, **Autocast** e **Nível** por habilidade da instância.
- **Ativa** controla se o override de `ModifiedAbility` é persistido; **Autocast** grava `activeForAutocast`; **Nível** grava `heroLevel`.
- A tabela agora mostra Ativa, Autocast e Nível em colunas separadas.

### v3.10.15

- Corrige textos do card de ícones invadindo/cortando a coluna do visualizador; o wrap agora acompanha a largura real do painel.
- Adiciona **Exportar ícones…** no visualizador de unidade/herói e em **UNIDADES NO MAPA**.
- Adiciona **Exportar ícones** ao lado de **Editar spell** nas habilidades de unidades e no editor de abilities da instância colocada.
- O ZIP de ícones procura BTN/DISBTN, PASBTN/DISPASBTN, ATC/DISATC e também `Art`, `Unart` e `Researchart` dos profiles Blizzard quando existirem.
- Mantém caminhos Warcraft no ZIP e inclui `icons_manifest.json`.


### v3.10.14
- Corrige o aviso falso de **Alterações não aplicadas** ao apenas navegar entre unidades colocadas no mapa.
- A detecção de edição agora usa um baseline estável da interface; diferenças inocentes de float32/ângulo não contam como alteração.
- Adiciona o ícone WC3 Object Studio fornecido pelo usuário à janela e taskbar/Alt-Tab do Windows.

# WC3 Reforged Object Studio v3.10.13 DEBUG

## v3.10.13 — visual das unidades no mapa + exportação de assets

- A aba **UNIDADES NO MAPA** agora possui ícone e preview 3D próprios para a instância selecionada, com DE / Reforged HD / Classic SD.
- A aba **Arte** de Units/Heroes, Spells/Abilities e Doodads ganhou **EXPORTAR MDX/MDL + TEXTURAS (.ZIP)**. O ZIP preserva a árvore de pastas do Warcraft, inclui as texturas referenciadas e grava um `export_manifest.json` com o mapeamento CASC/MPQ.
- O catálogo de abilities passa a mesclar os profiles Blizzard offline como baseline antes da localização do CASC; se uma locale omitir uma seção, o nome não cai mais no rawcode. Names placeholder iguais ao rawcode (ex.: `ACnr=ACnr`) não substituem um nome humano já conhecido.
- Overrides originais cujo `war3mapSkin.w3a` traz apenas o rawcode como Name agora exibem o nome nativo no browser/form sem apagar o dado original do mapa.



## v3.10.13 — aba dedicada Unidades no mapa + hotfix de startup

- Corrige `ModuleNotFoundError: wc3injector.editor_strings`: o módulo agora faz parte do pacote de atualização e o launcher verifica sua presença antes de abrir o app.
- Separa os dois níveis de edição em abas principais: **OBJECT EDITOR** para `war3map.w3u/.w3a/.w3d` e **UNIDADES NO MAPA** para instâncias de `war3mapUnits.doo`.
- Remove “Heróis no mapa” do combo de tipos do Object Editor, evitando misturar definição de objeto com placement.
- A nova aba lista todas as placements do mapa e permite filtrar **Todos / Heróis / Outras unidades e itens**, além de pesquisar por nome, rawcode, owner e creation number.
- Mantém o painel estilo World Editor: **Geral / Habilidades / Inventário / Itens Dropados**. Campos exclusivos de herói ficam preservados/inativos em placements não-herói.
- `SALVAR` também aplica automaticamente qualquer rascunho aberto na aba de placements antes do commit transacional.
- O console/launcher agora mostra a versão correta da build.


## v3.10.11 — Heróis colocados / war3mapUnits.doo

- Novo modo **Heróis no mapa** no browser esquerdo.
- Edita a instância colocada em `war3mapUnits.doo`, sem transformar a alteração em override global do tipo do herói.
- Aba **Geral**: owner/player, facing, HP%, mana%, target acquisition, nível, Strength, Agility e Intelligence.
- Aba **Habilidades**: lista as abilities do tipo e os overrides da instância; permite adicionar, editar rawcode, level e autocast.
- Aba **Inventário**: edita os 6 slots iniciais.
- Aba **Itens Dropados**: None, item table do mapa ou tabela custom com sets/chance.
- `war3mapUnits.doo` participa da transação/validação de bytes e da detecção de alterações externas. JASS/Lua/triggers/terreno continuam protegidos por hash; desde a v3.10.20, `war3map.j` aceita somente o patch marcado de runtime-sync quando uma propriedade de instância suportada realmente mudou.
- Parser detecta estruturalmente mapas legados e Reforged com `skinId` (1.32+).
- O controle **Spell level** foi movido para uma linha própria do cabeçalho para não ser cortado por nomes/metadados longos.

## v3.10.10 — nomes nativos completos

- Resolve tokens `WESTRING_*` via `WorldEditStrings.txt` do CASC, com fallback de idioma.
- Corrige abilities/doodads que apareciam apenas como rawcode no browser.

## v3.10.9 — Viewer Reforged HD: CAMS / EVTS

- Corrige modelos HD nativos que falhavam com `Incorrect camera chunk data EVTS`.
- O parser de `CAMS` agora respeita o limite real do chunk pai e não consome o próximo chunk top-level (`EVTS`, etc.) quando um modelo declara um tamanho de câmera maior que o payload disponível.
- Dados de câmera desconhecidos/estendidos passam a ser opcionais no preview: não derrubam mais geometria, materiais, animações e texturas do modelo inteiro.
- `setup_viewer.py` reaplica automaticamente o hardening CAMS/EVTS se o bundle `war3-model 4.0.1` for restaurado.
- Inclui regressão sintética que reproduz exatamente `CAMS` superdimensionado seguido de `EVTS`.
- Mantém integralmente o suporte DE/SKIN 16-bit e o renderer/DDS da v3.10.8.

## v3.10.8 — Viewer HD/DE: shader preto + DDS DX10

- Corrige o preview preto de vários modelos Reforged HD e Definitive DE mesmo quando o MDX e as texturas eram encontrados.
- Remove o limite GLSL global de 512 matrizes introduzido na v3.10.7; muitos WebView2/WebGL não conseguem linkar um shader desse tamanho.
- HD/DE agora monta uma paleta compacta apenas com os bones realmente usados pelo modelo e remapeia os índices `SKIN` para essa paleta, preservando `uint16` do patch 3.0 sem estourar uniforms da GPU.
- O tamanho do shader passa a ser dinâmico por modelo e respeita `MAX_VERTEX_UNIFORM_VECTORS` da GPU.
- Erros de compilação/link do shader deixam de falhar silenciosamente: aparecem no viewer e entram no log com capacidade de uniforms, quantidade de bones ativos e maior índice usado.
- Corrige inicialização indefinida da matriz de skinning em GLSL (`mat4 sum`) para evitar comportamento diferente entre drivers.
- O loader DDS passa a reconhecer cabeçalho `DX10` e DXGI BC1/BC2/BC3/BC5/BC7/RGBA8, além dos FourCC antigos.
- Quando uma textura não puder ser decodificada pela GPU/browser, o viewer injeta uma textura neutra de fallback para a geometria continuar visível em vez de virar um quadro preto.
- Mantém o suporte DE/CASC e o parser `SKIN` 16-bit da v3.10.7 e a persistência `war3mapSkin` da v3.10.6.

## v3.10.7 — Definitive Edition (DE) / Patch 3.0 CASC

- Adiciona o terceiro modo gráfico nativo **Definitive DE** ao preview, mantendo **Reforged HD** e **Clássico / SD**.
- Resolve assets DE no CASC por `war3.w3mod:_de.w3mod:<path>`, com fallback DE → HD → SD sem redirecionar SLK/TXT/metadados.
- Reconhece `file:de`, `Model:de`, `Art:de`, `Icon:de` e variantes em perfis nativos.
- Atualiza doodads/unidades e troca de idioma para preservar corretamente o modo DE selecionado.
- Corrige o parser MDX embutido para o `SKIN` largo do patch 3.0 (uint16), detectado estruturalmente em vez de pelo `VERS`.
- WebGL passa a aceitar índices/pesos de skin em 8 ou 16 bits e amplia o limite de matrizes de ossos para os rigs DE.
- Mantém todas as correções de persistência do `war3mapSkin` da v3.10.6.

## v3.10.6 — gravação Reforged completa: war3map + war3mapSkin

- corrige o caso em que modelo, escala, ícone, nome e outros campos visuais apareciam alterados no World Editor, mas o jogo ainda usava os valores antigos;
- mapas Reforged dividem Object Editor entre `war3map.w3u/.w3a/.w3d` e `war3mapSkin.w3u/.w3a/.w3d`; a build anterior lia a camada Skin, mas a tratava como somente leitura;
- ao editar um campo visual/localizável, o Studio agora sincroniza somente os campos realmente modificados para a camada `war3mapSkin`, preservando gameplay e dados não relacionados;
- a classificação de campos Skin é aprendida a partir do próprio mapa, com fallback para campos visuais essenciais como `umdl` (modelo) e `usca` (escala), cobrindo também Raw Fields usados por mapas custom;
- remover um override visual também remove a cópia correspondente da camada Skin, evitando que um valor antigo continue prevalecendo no runtime;
- `war3mapSkin.w3u/.w3a/.w3d` passam a fazer parte do snapshot de arquivos editáveis, então alterações externas feitas pelo World Editor são detectadas antes do commit;
- `war3mapSkin.txt` e categorias de objeto não suportadas continuam protegidas; JASS/Lua/triggers/terreno/placements permanecem intocados;
- validação pós-save agora compara os bytes reabertos do MPQ com os bytes exatos esperados em memória, além do round-trip estrutural;
- adiciona regressões automáticas para modelo + escala, gameplay-only, remoção de override e Raw Field visual; suíte completa: 133 testes.

## v3.10.5 — Proteção de rascunho + inglês revisado + validação de gravação

- ao clicar em outro objeto com alterações ainda não aplicadas, mostra **Alterações não salvas / Unsaved changes** e pergunta se deseja sair e descartar o rascunho;
- cancelar a troca restaura a seleção anterior sem recarregar o formulário nem apagar o que estava sendo editado;
- alterações já aplicadas à `EditSession` não geram falso aviso, porque continuam seguras em memória até **SALVAR / SAVE**;
- revisa textos do modo inglês em preview 3D, ícones, Spell Lab, diálogos, mensagens de erro, status, proveniência de valores nativos e modo Classic/SD;
- corrige a ordem da tradução de proveniência para não produzir frases híbridas como `ícone inherited from Warcraft`;
- mantém as correções da v3.10.4 para bandeira dos EUA, listas repetidas de doodads e modelos MDL estáticos sem `Sequences`;
- a validação final de save altera apenas o object-data necessário (`war3map.w3u`) e preserva scripts/triggers/arquivos protegidos por hash.

## v3.10.4 — Bandeira US correta + doodads repetidos + modelos estáticos

- corrige `assets/us.png`: 13 faixas e representação legível das 50 estrelas no ícone 30×20;
- corrige doodads Reforged cujos campos repetidos chegam como listas CSV (`255,255,...`) em vez de colunas numeradas;
- evita `ValueError` ao abrir doodads como `YSw4`/`WSl0`;
- modelos MDL estáticos sem bloco `Sequences` recebem uma sequência sintética de 1 frame apenas no viewer, evitando crash em `ModelRenderer.setSequence`;
- nenhuma dessas correções altera JASS, triggers ou gameplay do mapa.

## v3.10.3 — Doodads completos + valores nativos + ícones clicáveis

- Corrige o crash ao abrir doodads como `ZPms`, `ZSb1` e `ZRrk`: os sentinelas nativos `-` / `_` não são mais convertidos por `float()`/`int()`; passam a significar “sem valor”.
- Carrega `Doodads\DoodadSkins.txt` do CASC Reforged e mescla `file:hd` / `file:sd` sobre `Doodads.slk`. Isso recupera modelos de doodads como `WSl0`, `YSwA` e outros cuja linha do SLK moderno não possui mais `file`.
- O preview de doodads respeita **Reforged HD** e **Clássico / SD** também nos caminhos vindos de `DoodadSkins.txt`.
- Campos de Unit/Hero que aparecem vazios apesar de existirem no Warcraft agora combinam `UnitFunc/UnitSkin`, `UnitUI.slk` Reforged e uma tabela Classic UnitUI somente para preencher colunas ausentes.
- Campos Profile do Reforged reconhecem variantes `campo:hd`, `campo:sd`, `campo_hd` e `campo_sd`; isso cobre `Art - Scaling Value`, model file, selection scale, tinting e outros valores movidos para skins.
- `Combat - Defense Type` e demais campos de SLK usam lookup de coluna case-insensitive para evitar lacunas por variação de capitalização.
- Os três ícones do painel (**Normal BTN / Disabled / Passive**) agora são clicáveis. O clique abre o arquivo BLP/TGA/DDS, faz stage no MPQ e atualiza o campo do ícone Normal quando necessário.
- Na primeira execução da build, o setup tenta baixar também `unitUIClassic.slk` como compatibilidade. Se a instalação local expuser UnitUI Classic/SD no CASC, ele é usado antes desse fallback.
- Adiciona testes de regressão para sentinela de doodad, `DoodadSkins` HD/SD, `modelScale:hd`, Defense Type e variantes de ícone.

## v3.10.2 — estabilidade nativa + Classic/SD correto + viewer mais leve

- Corrige um vazamento pesado de widgets Tk ao trocar de objeto: as abas antigas do formulário eram apenas removidas do `Notebook`, mas seus widgets continuavam vivos. Agora o schema do formulário é reutilizado quando possível e árvores antigas são realmente destruídas quando precisam ser reconstruídas.
- Adiciona contadores de recursos do processo no debug (`handles`, `GDI`, `USER objects`, threads) e `logs/fatal_native.log` via `faulthandler`, para diagnosticar crashes nativos sem traceback.
- Corrige o cache do preview quando um modelo custom ausente cai para a arte nativa: HD e Clássico/SD agora usam chaves distintas, então trocar o seletor não reaproveita por engano o modelo HD.
- O servidor HTTP interno do viewer deixa de criar uma thread nativa por request e passa a usar pool limitado a 8 workers. Isso reduz churn de handles/threads ao navegar rápido por personagens e skills.
- O viewer libera renderers/WebGL antigos antes do hot-swap, pausa o render durante a carga e registra `rendererMs` / `textureMs` por modelo para separar gargalo de parsing, textura e GPU.
- Skills sem Art 3D deixam de virar `ERROR`/traceback: o painel informa que não há modelo 3D detectável, porque isso é válido para várias abilities.
- Referências idênticas de FX são deduplicadas antes da extração/render. Isso evita carregar o mesmo MDX e as mesmas texturas várias vezes na mesma skill.
- Mantém as correções de doodads nativos, fallback visual, CASC persistente e cache 3D da build anterior.

### Mantido da v3.10.1

- Corrige doodads nativos como `YSca`, `JSar`, `ZSas`, `KOst` e outros quando o `Doodads.slk` do CASC retorna a linha mas deixa `file`/Art - Model File vazio: os campos ausentes são preenchidos pelo snapshot clássico local sem substituir dados válidos do Reforged.
- O resolver de doodad aceita `modelPath`, `file`, `dir + file` e expande `numVar` em `Base0`, `Base1` etc.
- Se um modelo custom/skin do mapa aponta para um import que não existe mais, o preview de unidade tenta a arte da unidade-base apenas como fallback visual; o mapa não é alterado.
- O debug agora imprime as chaves/campos recebidos do doodad quando nenhum modelo puder ser resolvido.
- O WebView mantém cache LRU dos arquivos de sessão e do MDX/MDL já parseado. Voltar para um personagem já aberto evita baixar/copiar e parsear tudo de novo.

### Mantido da v3.10.0

- WebView 3D permanece vivo entre personagens: troca por hash, sem recarregar HTML/JS/WebGL inteiro.
- CASC fica aberto e compartilhado durante a sessão, removendo o custo de abrir/fechar Reforged a cada clique.
- Cache 3D separado por modo visual quando necessário; fallback nativo de um custom ausente nunca vaza de HD para Clássico/SD (nem o contrário).
- Workers antigos são cancelados quando o usuário clica rapidamente em outro objeto.
- Preview 3D recebe prioridade sobre ícones/skills; tarefas secundárias começam alguns ms depois.
- Debug de performance PREVIEW[...] e VIEWER_JS com tempos, bytes, cache hit/miss e erros do renderer.
- Overlay de carregamento dentro do próprio viewer para transições mais limpas.

- Corrige o preview **Clássico / SD** no WebView: modelos MDX v800 usam o LOD disponível do modelo (normalmente LOD 0), em vez de serem renderizados à força no LOD 2 do Reforged. Esse era o motivo de o Acolyte carregar modelo/texturas/animação e ainda assim ficar totalmente preto.
- Corrige a prioridade do CASC: **Classic/SD = `war3.w3mod:<path>`** e **Reforged HD = `war3.w3mod:_hd.w3mod:<path>`**. `war3sd.w3mod` fica apenas como fallback de compatibilidade.
- Passa a ler `war3mapSkin.w3u`, `war3mapSkin.w3a` e `war3mapSkin.w3d` como overlay read-only do Object Editor Reforged. Assim nomes, modelos e ícones que não estão no `war3map.w3*` principal deixam de desaparecer.
- Lê `war3map.wts` para resolver `TRIGSTR_xxx` usados pelo skin do mapa. No OPWU 0.8 isso recupera, entre outros, Big Mom, Aramaki, Kaido, Shanks, Luffy, Fujitora, Franky e suas formas/variantes.
- Os arquivos `war3mapSkin.*` ficam protegidos por hash e não são regravados implicitamente ao salvar.
- O catálogo de skin ganhou índice por rawcode para evitar varrer centenas de objetos a cada campo do formulário.

## v3.9.7 — Preview FX-only + fallback de modelos nativos

- O painel 3D de habilidades não monta mais herói/caster/alvo nem exibe **TESTAR HABILIDADE**. Ele mostra somente os modelos 3D da skill (Caster/Target/Special/Effect/Area/Missile Art, Buff/Effect e referências locais do script), com seletor de FX.
- FX curtos reiniciam automaticamente para partículas/projéteis não sumirem do preview.
- O card separado **Teste da Habilidade** não é mais exibido na lateral.
- Preview de unidades nativas tenta múltiplos caminhos de modelo, incluindo fallback de modelos cinematics antigos `*CIN` para o modelo gameplay equivalente e unidades irmãs com o mesmo ícone. Isso cobre casos como `uabc` / `AbominationCIN` no Reforged.
- O seletor **Reforged HD / Clássico SD** continua sendo somente visualização; não altera o mapa.


## v3.9.6 — Reforged HD / Clássico SD

O visualizador agora tem um seletor de modelos entre **Reforged HD** e **Clássico / SD (pré-Reforged)**. O modo altera apenas a origem dos assets usados no preview via CASC; nenhum dado do mapa é modificado. Unidades, heróis e doodads nativos respeitam a escolha. Efeitos de spell continuam usando fallback Classic quando necessário para partículas compatíveis com o renderer embutido.

## v3.9.5 — Spell FX visíveis + Doodads com variações

- O preview de efeitos nativos tenta primeiro os modelos **SD/Classic** do CASC, porque muitos efeitos HD do Reforged são stubs de Popcorn FX que o renderer WebGL atual não desenha. Heróis, unidades e doodads continuam usando os modelos HD normalmente.
- Auras ficam visíveis na cena e os outros casts são repetidos automaticamente a cada poucos segundos, para projéteis/impactos/partículas não sumirem antes de você olhar o painel.
- Doodads passam a expandir `file + numVar` (`Modelo0`, `Modelo1`...) ao procurar o MDX/MDL, que é o formato usado por vários objetos nativos do Warcraft.
- `WESTRING_DOOD_*` deixa de aparecer como nome do doodad quando o próprio `Doodads.slk` possui um `comment` legível.
- O botão **Abrir externo** foi removido; o preview fica concentrado no visualizador embutido.
- A busca de FX em `war3map.j` agora fica restrita à função/bloco da habilidade; ela não varre mais ~20k caracteres e mistura efeitos de spells vizinhas.
- Custom abilities baseadas em `ANcl` (Channel) deixam de puxar a arte-placeholder nativa quando o script local fornece os efeitos reais.
- Tenta os namespaces locais do CASC e, quando `*AbilityStrings.txt` não abre por nome, usa os profiles oficiais baixados pelo `setup_native_data.py` como fallback offline.
- Nomes de abilities padrão e de spells custom herdadas deixam de cair no rawcode quando o texto existe no Warcraft.
- Inclui `ItemAbilityFunc/Strings` no catálogo nativo.
- Buffs/Effects auxiliares `Bxxx`/`Xxxx` continuam disponíveis para resolver Art, mas não aparecem mais como se fossem spells na árvore Warcraft.
- Lê `AbilitySkin.txt` como overlay de ícone do Reforged, inclusive `Art:sd`, para o preview 2D usar a arte correta.
- Corrige rawcodes de localização com diferença apenas de maiúsculas/minúsculas quando há um único rawcode canônico correspondente.
- O Spell Lab agora segue `BuffID` e `EfctID`: efeitos guardados em perfis `Bxxx`/`Xxxx` também entram na cena 3D.
- Listas de modelos em `Caster/Target/Special/Effect/Area/Missile Art` são preservadas e todos os modelos são carregados, em vez de serem confundidos com níveis da ability.
- Custom abilities herdam nome, ícone e art visual do rawcode-base quando não possuem override no `war3map.w3a`.
- Mantém MPQ com prioridade sobre CASC, igual ao comportamento de override do jogo.
- Adiciona testes de regressão específicos para locale VFS, case de rawcode, AbilitySkin e Buff/Effect art.
- O Spell Lab passa a montar cenas automáticas: aura = herói base + aliado; alvo aliado = caster + aliado; alvo inimigo = caster + inimigo; self/instantânea = um herói base.
- O cast vira uma demonstração curta e volta para `Stand`; falha de preview limpa a cena antiga para não deixar um modelo enganoso na tela.
- Doodads agora usam `DoodadMetaData.slk` para expor o schema nativo completo (modelo, escala, seleção, variações, pitch/roll, pathing, fog, minimapa, tint etc.) e resolver seus valores em `Doodads.slk`.
- Texturas relativas de modelos nativos também são procuradas perto do caminho do MDX/MDL no CASC, importante para doodads e destructibles.


## v3.9.1 — hotfix de bindings dos mixins

- Corrige helpers estáticos que perderam `@staticmethod` durante a modularização.
- Elimina o crash ao selecionar unidades/heróis em `AbilityListMixin._split_rawcodes`.
- Protege também preview de modelo, Spell Lab, resolução de assets e variantes de ícone contra o mesmo tipo de regressão.
- Adiciona testes de binding específicos para mixins.

## v3.9.1 — arquitetura modular

A interface e o núcleo de edição foram reorganizados por responsabilidade, sem alterar o formato dos mapas nem liberar escrita em JASS/Lua/triggers/terrain.

- `app.py` agora é somente o bootstrap da GUI.
- `wc3injector/ui/object_studio.py` compõe a janela principal a partir de mixins pequenos.
- `ui/object_browser.py`, `object_form.py` e `object_commands.py` separam navegação, formulário e ações de edição.
- `ui/archive_asset_resolver.py` resolve caminhos MPQ/imports; `icon_preview.py` cuida apenas de ícones.
- `ui/ability_list.py` cuida da lista de abilities da unidade; `spell_lab.py` resolve candidatos/efeitos do laboratório.
- `ui/model_preview.py`, `embedded_viewer.py` e `webview_focus.py` isolam preview 3D, host WebView2 e foco do teclado.
- `wc3injector/core/map_edit_session.py` concentra a sessão de edição do mapa.
- `wc3injector/core/map_integrity.py` concentra snapshots SHA-256, arquivos protegidos e carregamento das tabelas `w3u/w3a/w3d`.
- `wc3injector/editor.py` permanece como import de compatibilidade para scripts antigos.
- `wc3injector/paths.py` centraliza caminhos internos do projeto.

A intenção é evitar novamente um arquivo central de milhares de linhas e deixar cada módulo com um nome que revele o que ele realmente faz.


## v3.6.1 — modelo/ícone nativo realmente vindo do CASC

- Para Unit/Hero padrão, o preview agora combina `UnitFunc/UnitSkin` com `UnitUI.slk` lido diretamente do CASC local.
- O modelo usa `UnitUI.slk:file` quando o profile não possui `file`, cenário comum em unidades/heróis vanilla como `Nalc`.
- Ícones de objetos nativos preferem o caminho resolvido do CASC instalado, não o baseline offline.
- O viewer continua somente leitura: nada é gravado na instalação do Warcraft.

## v3.6 — objetos padrão usam o CASC de verdade

Quando a linha selecionada é um objeto **Warcraft** (sem override dentro do mapa), o painel visual não procura mais o MDX/BLP apenas no MPQ. Agora ele usa a instalação local detectada pelo botão **CASC…**.

- Units/Heroes padrão: resolve `UnitFunc` + `UnitSkin`, busca BTN/DISBTN/PASBTN no CASC e extrai o modelo nativo + texturas.
- Spells padrão: ícones `aart` e variantes também usam CASC quando não estão no mapa.
- Habilidades listadas numa unidade padrão: os ícones podem vir diretamente do CASC.
- Doodads padrão: o modelo vindo do `DoodadData.slk` é extraído do CASC.
- Model paths sem extensão tentam `.mdx` e `.mdl`; caminhos `.mdl` também tentam a versão `.mdx`, útil no Reforged.
- O MPQ continua tendo prioridade para objetos/imports do mapa. CASC é somente leitura e nunca altera a instalação do Warcraft.

## v3.5 — navegador mostra também objetos padrão do Warcraft

O navegador da esquerda agora tem o filtro **Todos / Mapa / Warcraft**.

- **Todos**: custom + overrides do mapa + objetos padrão do jogo.
- **Mapa**: somente objetos realmente gravados em `war3map.w3u`, `war3map.w3a` e `war3map.w3d`.
- **Warcraft**: somente objetos padrão que ainda não possuem override no mapa.

O catálogo padrão inclui Units/Heroes da base nativa, Abilities/Spells lidas do CASC local e, quando `DoodadData.slk` estiver disponível no CASC, Doodads padrão. Ao editar um objeto marcado como **Warcraft**, clicar em **APLICAR NO OBJETO** cria automaticamente um override na tabela original do mapa; o objeto base do jogo nunca é alterado.


## v3.4 — ajuda de verdade + Spell Lab/CASC

- Cada campo editável tem `?` com explicação em PT-BR do que o valor muda **dentro do jogo**, não só o raw field/SLK.
- O balão mantém os detalhes técnicos (rawcode, tabela, coluna, min/max) abaixo da explicação.
- Corrige paths de efeitos vindos do JASS com barras duplicadas (`Abilities\\Spells...`) antes de procurar no MPQ/CASC.
- O CASC local agora consegue receber o caminho normalizado para tentar carregar efeitos vanilla usados por spells scriptadas.
- Mantém UI adaptativa, Spell Lab, preview 3D, MPQ read-only para preview e proteção de scripts/triggers no save.

Editor/injetor Python para `.w3m` / `.w3x`, focado em **Units/Heroes, Abilities/Spells, Doodads e imports MDX/MDL/BLP/TGA/DDS**. A build DEBUG mantém o CMD aberto e grava `logs/latest.log` com traceback completo.




## O que mudou na v3.3.1 — abas realmente responsivas

O formulário central do Object Editor não usa mais uma grade rígida com colunas que empurravam `origem`, `help` e nomes grandes para fora da área visível. Cada propriedade agora é uma linha adaptativa que mede o espaço real disponível dentro da aba.

- em telas largas: **nome + campo + ações + origem** ficam na mesma linha;
- em largura média: origem e ajuda descem automaticamente para linhas próprias;
- em painel estreito: **nome, campo, botões, origem e ajuda** ficam empilhados, sem cortar texto;
- labels longas vindas do `UnitMetaData.slk` usam quebra de linha em pixels e respeitam DPI/fontes do Windows;
- `Importar MDX/MDL` e `Visualizar` também reposicionam quando não há largura suficiente;
- o conteúdo continua com scroll vertical, mas não precisa de scroll horizontal para enxergar a ficha da unidade.

A mudança vale para todas as abas geradas pelo metadata: **Geral, Habilidades, Arte, Stats, Movimento, Combate, Herói** e as categorias das Abilities/Doodads.


## O que mudou na v3.2 — Spell Lab funcional + CASC local

A v3.2 foca no teste visual de habilidades dentro do viewer:

- o HUD do viewer embutido ficou **bem menor** e mais transparente; em modo compacto ele esconde Team Color, slider de zoom e wireframe, porque zoom continua disponível pela roda do mouse;
- **botão esquerdo** gira a cena; **botão direito** arrasta/move o herói e todos os efeitos; **roda** dá zoom; botão do meio sobe/desce a câmera;
- `TESTAR HABILIDADE` reinicia a sequence do herói e todas as sequences dos efeitos; a escolha automática agora prioriza `Spell`, `Channel`, `Slam` e `Cast` antes de cair em `Attack`;
- Caster/Special, Missile e Target/Area são mostrados numa pequena timeline visual em vez de todos nascerem eternamente ao mesmo tempo;
- o seletor **Efeito** continua disponível no HUD compacto quando a habilidade realmente possui efeitos carregados.

### Fallback para o CASC instalado do Warcraft III

Se um modelo, textura, `Animnames` ou Art da habilidade não estiver dentro do MPQ, o Studio pode ler os assets **diretamente da instalação local do Warcraft III Reforged** via CASC. O botão **CASC…** permite apontar manualmente para a pasta do jogo quando a detecção automática não encontrar a instalação.

A resolução do Spell Lab agora tenta, nesta ordem:

1. override da habilidade no `war3map.w3a`;
2. Art/Animnames herdados da ability base lidos do CASC;
3. referências literais de `.mdx/.mdl` próximas ao rawcode em `war3map.j` / `war3map.lua` — **somente leitura**, nunca execução nem edição;
4. imports do mapa cujo nome combina fortemente com o nome/ícone da spell;
5. CASC para completar modelos ou texturas nativas que não estejam no MPQ.

Isso é especialmente útil para mapas como OPWU, em que várias skills são montadas por script e o `w3a` pode conter números/tooltips mas nenhum `Caster Art`, `Target Art` ou `Effect Art`. Se o efeito for criado por variável/hashtable e não existir nenhum caminho literal ou import reconhecível perto da spell, o Studio não inventa o asset: o log informa que nenhum efeito foi localizado.

### Herói de teste para skills dinâmicas

Quando nenhuma unidade possui o rawcode diretamente em `uabi/uhab`, o fallback agora ranqueia os heróis usando o **nome e o ícone da spell**. Assim uma skill cujo BTN contém `kaido` tende a selecionar um modelo do Kaido em vez de simplesmente o primeiro herói custom da lista.

`setup_casclib.py` prepara a ponte CASC no Windows. Nenhum modelo/textura nativo do Warcraft é empacotado junto com o Studio; os assets são lidos da instalação local do usuário.


## O que mudou na v3.1 — laboratório visual de habilidades

Ao selecionar uma **Ability/Spell**, o painel 3D deixa de ficar vazio. O Studio agora monta uma cena de teste usando um herói do próprio mapa:

- procura primeiro Units/Heroes que realmente possuam o rawcode da habilidade em `Abilities - Normal`, `Hero`, `Normal Skin` ou `Hero Skin`;
- quando existem vários usuários, aparece um seletor **Herói de teste** no painel direito;
- se nenhuma unidade referencia diretamente a spell, o Studio oferece um herói custom com modelo disponível no MPQ como dummy visual;
- o herói continua usando o MDX/MDL e as texturas extraídas diretamente do mapa;
- o seletor **Animação** passa a controlar as sequences do herói;
- o botão **TESTAR HABILIDADE** tenta escolher automaticamente a animação indicada por `Animnames` e reinicia a cena;
- o seletor **Efeito** permite ver todos, nenhum ou um slot visual isoladamente.

O laboratório lê os principais slots visuais do Object Editor da ability: **Caster Art, Target Art, Special Art, Effect Art, Area Effect Art e Missile Art**. Os MDX/MDL desses slots também são extraídos do MPQ com suas texturas. Caster/Special aparecem junto do herói, Target/Area em um ponto à frente, e Missile faz um percurso visual entre caster e target.

Isso é um **preview visual**, não uma simulação da engine: não executa dano, targeting, triggers/JASS/Lua, buffs ou a lógica interna da habilidade. A v3.2 pode **ler** JASS/Lua em modo read-only para localizar caminhos literais de modelos usados por skills scripted, mas nunca executa nem altera esses scripts. Attachment points e trajetória são aproximados para inspeção rápida de modelo/animação/efeito.

## O que mudou na v3.0 — valores herdados reais + Object Editor completo

A grande mudança desta versão é que o Studio deixa de tratar `war3map.w3u` como se ele contivesse o objeto inteiro. O arquivo do mapa normalmente guarda **somente os campos alterados**. Um custom baseado em `Hmkg`, por exemplo, pode ter 5–10 overrides próprios enquanto centenas de propriedades continuam vindo dos dados nativos do Warcraft.

Na primeira execução (e rapidamente em cada `run_gui.bat`, apenas para conferir o cache), `setup_native_data.py` prepara uma base local em `native_data/` com os SLKs de Units/Heroes usados pelo Object Editor:

- `UnitMetaData.slk` — schema/campos do Object Editor;
- `UnitData.slk`;
- `UnitUI.slk`;
- `UnitBalance.slk`;
- `UnitWeapons.slk`;
- `UnitAbilities.slk`;
- profiles `*UnitFunc.txt` / `*UnitStrings.txt` para campos como ícone, button position, projectile art etc.

O mapa continua tendo prioridade absoluta. A resolução agora é:

1. override no objeto selecionado;
2. override herdado de outro objeto do próprio mapa;
3. valor nativo da base Blizzard (`Hmkg`, `hfoo`, etc.).

Por isso HP, mana, visão, armor, movement, attack, art e outros valores herdados passam a aparecer **preenchidos**, com a origem ao lado, em vez de uma caixa vazia dizendo apenas “herdado do Warcraft”. Alterar um valor herdado cria somente o override necessário no `w3u`; deixar como está não duplica centenas de campos.

### Campos muito mais próximos do World Editor

A interface de Units/Heroes agora é gerada a partir de `UnitMetaData.slk`, em vez de uma lista curta hardcoded. Isso expõe centenas de propriedades organizadas em Geral, Habilidades, Arte, Stats, Movimento, Combate e Herói, incluindo campos do tipo:

- `Abilities - Default Active Ability`;
- `Abilities - Hero`;
- `Abilities - Normal`;
- `Abilities - Hero Skin` / `Normal Skin` (Reforged);
- `Art - Allow Custom Team Color`;
- animation blend/cast/run/walk;
- button position;
- caster upgrade art;
- death time;
- elevation/fog sample settings;
- water shadow;
- game-interface/score-screen icon;
- maximum pitch/roll;
- model file + extra versions;
- occluder height/orientation interpolation;
- projectile launch/impact XYZ;
- required animation/attachment/bone names;
- scaling/selection circle/tint;
- os campos completos de combat, movement, stats e hero disponíveis no metadata.

`Raw Fields` continua existindo para qualquer campo raro ou futuro que ainda não esteja no schema carregado.

### Preview de formas custom continua correto

Carregar o valor nativo de `umdl/uico` não faz o preview de uma transformação voltar para o Mountain King só porque ela deriva de `Hmkg`. Para objetos custom, o visualizador prefere um visual custom compatível/sibling quando o único modelo/ícone disponível no formulário vem da base nativa. O valor nativo continua visível e editável na aba Arte, mas o preview continua mostrando a forma correta quando ela existe no MPQ.

**Importante para quem atualizar pelo hotfix:** rode `run_gui.bat`. Ele chama `setup_native_data.py` automaticamente. Na primeira vez precisa de internet para baixar os pequenos arquivos de metadata/base; eles ficam em cache depois.


## O que mudou na v2.10

### Ícones do herói/unidade separados de ícones de spells

O painel acima do viewer 3D agora é uma galeria de **ícones da unidade/herói**. Ele tenta parear variantes do mesmo command button por basename e mostra, quando estiverem importadas no mapa:

- **Normal BTN** (`ReplaceableTextures\CommandButtons\BTN...`);
- **Disabled** (`CommandButtonsDisabled\DISBTN...`);
- **Passive** (`PassiveButtons\PASBTN...`).

A inferência de `uico` ficou mais rígida: um botão como `BTN_OPWU_fujitora_01_gravity_press.blp` não pode mais virar o retrato do Fujitora só porque contém o nome do personagem. Botões numerados/com termos extras de spell são tratados como skill icons. Se não houver retrato custom confiável dentro do MPQ, o Studio informa que o objeto herda o ícone nativo em vez de mostrar uma spell errada.

### Aba Habilidades virou uma lista visual

Para Units/Heroes, a aba **Habilidades** mantém os campos `uabi` e `uhab`, mas agora também cria uma lista visual abaixo deles. Cada rawcode mostra:

- ícone da ability (`aart`) extraído do próprio MPQ;
- rawcode;
- nome da spell (`anam`, com fallback para tooltip quando disponível);
- origem **Normal** / **Herói**;
- origem do ícone;
- botão **Editar spell**, que troca a árvore para Abilities e abre aquele rawcode.

Os ícones das habilidades são carregados em lote com uma única abertura read-only do MPQ, respeitando o lock por mapa já usado pelo visualizador. Ícones de skills ficam na aba Habilidades e não contaminam mais a galeria de retrato do personagem.

## O que mudou na v2.9

### Correção de teclado/foco com o viewer 3D embutido

No Windows o WebView2 é um HWND filho real. Em algumas combinações de Windows 11 + Python 3.14 + Tk 8.6 ele continuava sendo o dono do teclado mesmo depois de clicar em um `Entry`, então a busca e os campos pareciam travados.

A v2.9 adiciona uma ponte explícita de foco usando a API pública `WebView.focus_parent()` do tkwry:

- o viewer é criado com `focused=False`;
- clique/foco em `Entry`, `Spinbox`, `Combobox` ou `Text` devolve o teclado ao Tk;
- o widget clicado recupera `focus_set()`/`focus_force()` quando necessário;
- após cada navegação do preview o WebView2 devolve o teclado ao editor;
- clicar dentro do viewport 3D continua permitindo rotação/zoom normalmente;
- o log DEBUG registra `Focus bridge: WebView2 -> Tk` para diagnosticar qualquer máquina em que o Windows mantenha foco nativo preso.

O MPQ, extração do modelo, ícones e perfil gravável do WebView2 continuam iguais à v2.8.

## O que mudou na v2.8

### WebView2: correção real do `HRESULT 0x80070005 / Acesso negado`

O log da v2.7 mostrou que a camada MPQ já está correta: o mapa abre, o MDX do Fujitora é encontrado e lido, e as três texturas BLP são extraídas do próprio MPQ. A falha restante ocorre antes da renderização, na criação nativa do WebView2.

A v2.8 não usa mais `WebSession(ephemeral=True)`. O viewer cria um `WebSession(data_directory=...)` com uma pasta explicitamente gravável em:

`%LOCALAPPDATA%\WC3_Reforged_Object_Studio\WebView2\session-<PID>`

O app faz um teste real de escrita nessa pasta antes de iniciar o WebView2 e também exporta `WEBVIEW2_USER_DATA_FOLDER` para o runtime nativo. Em retry, usa outra pasta por processo. O caminho aparece no CMD/log.

### Ícone: não confundir textura do modelo com Command Button

A v2.7 inferiu `lssho001_body_d.blp` como se fosse o ícone do Fujitora porque o arquivo compartilhava os mesmos tokens de nome/pasta. Isso era visualmente errado.

Na v2.8 a inferência automática aceita somente assets que realmente tenham cara de ícone do Warcraft: caminho `ReplaceableTextures\CommandButtons` ou nome começando por `BTN`/`DISBTN`. Texturas de body/cloak/weapon/diffuse/normal etc. são descartadas como ícone.


## O que mudou na v2.7

### Correção do erro 32 ao carregar modelo + ícone
Ao selecionar uma unidade, a v2.6 disparava dois workers ao mesmo tempo: um para o ícone e outro para o MDX/MDL. Cada worker criava um `SFileOpenArchive` separado para o mesmo mapa. Em algumas builds da StormLib/Windows isso causa `ERROR_SHARING_VIOLATION (32)`: um worker consegue ler o ícone e o outro falha ao abrir o mesmo MPQ.

A v2.7 adiciona um **lock por caminho de mapa** na própria camada StormLib. Leituras do mesmo `.w3m/.w3x` agora entram numa fila curta e reutilizam o MPQ de forma segura: primeiro uma extração termina e fecha o handle, depois a próxima abre. Mapas diferentes continuam podendo ser processados em paralelo.

Além disso, operações que só leem (abrir mapa, ícones, preview 3D e validações) usam `MPQ_OPEN_READ_ONLY`. Gravação continua exclusivamente na cópia transacional. O log agora registra `Waiting/Acquired/Released MPQ path lock` e `flags=0x100` para operações de preview.

## O que mudou na v2.6

### Visualizador foi para a direita

A interface agora tem três colunas:

- **esquerda:** lista/pesquisa de Units, Spells e Doodads;
- **centro:** Object Editor (stats, art, movement, combat, hero, abilities e Raw Fields);
- **direita:** **VISUALIZADOR**, com ícone + modelo 3D.

O preview não ocupa mais a parte inferior da lista.

### Fujitora / formas que não possuem `umdl` próprio

Alguns objetos de transformação/quest do OPWU não repetem o campo de modelo. Exemplo real:

- `EY1S` — **Fujitora (Gravity Admiral)**, base `Hmkg`, não grava `umdl`;
- `H004` — outra forma do Fujitora com a mesma base e a ability `FJ06`, grava:
  `war3mapImported\\OPWU_STORMLIB\\fujitora2\\fujitora.mdx`.

A v2.6 possui um **resolver visual somente para preview**. Quando o objeto selecionado não tem `umdl`, ele procura um sibling compatível pela base, abilities/família do personagem e escala. Para `EY1S`, o resolver encontra `H004` e usa o MDX dele no painel visual.

Isso **não cria nem altera `umdl` no mapa**. É só uma resolução para visualizar o personagem correto sem modificar object-data.

### Ícones dentro da UI

O painel da direita também mostra o ícone selecionado.

Suporte via Pillow para:

- BLP;
- TGA;
- DDS;
- PNG/JPG.

Prioridade:

1. `uico` da Unit/Hero ou `aart` da Ability;
2. valor herdado de outro override do próprio mapa;
3. sibling visual compatível;
4. inferência conservadora nos imports `BTN...` usando nome/modelo do personagem.

A inferência é apenas visual e **nunca é gravada automaticamente no mapa**.

Se o ícone for nativo do Warcraft e não estiver importado no MPQ, o Studio informa isso em vez de inventar um arquivo.

### WebView2: correção do `0x80070005 / Access denied`

O preview 3D embutido agora cria um `WebSession(ephemeral=True)` do `tkwry`. Isso usa um perfil temporário e evita depender da pasta padrão de dados do WebView2, que pode ficar sem permissão ou bloqueada.

Também há:

- retry automático uma vez com um perfil temporário novo;
- botão `↻` do painel visual que também reinicializa o viewer se ele tiver falhado;
- mesmo se o WebView2 falhar, o Studio ainda resolve/extrai o modelo e permite **Abrir externo**.

## Primeira execução

Use:

`first_run.bat`

Ele instala as dependências (`tkwry` + Pillow), prepara StormLib, renderer MDX/MDL, base nativa do Object Editor e a ponte CASC. Se o Warcraft III estiver em uma pasta incomum, clique em **CASC…** e selecione a pasta do jogo.

Depois:

`run_gui.bat`

Mantenha o CMD aberto. O log completo fica em:

`logs\\latest.log`

## Campos herdados

`war3map.w3u/.w3a/.w3d` normalmente guardam só overrides. Para Units/Heroes, a v3.1 carrega os dados-base do Object Editor e diferencia:

- `override`;
- `herdado de XXXX no mapa`;
- `base nativa XXXX · UnitBalance/UnitUI/UnitWeapons/...`;
- `base nativa XXXX · Profile...`.

O valor herdado é mostrado no campo, mas não vira override enquanto você não o alterar. Abilities e Doodads continuam preservando a lógica segura existente; o Raw Fields mostra exatamente o que está armazenado no mapa.

## Arquivos que o Studio pode alterar

- `war3map.w3u` — Units/Heroes;
- `war3map.w3a` — Abilities/Spells;
- `war3map.w3d` — Doodads;
- `war3map.imp`;
- assets importados pelo usuário.

JASS, Lua, triggers, terrain e placement continuam protegidos pelo save transacional e por hashes.

Object Data suportado: versões `0`, `1`, `2` e `3` (Reforged).

## Testes

A suíte desta build tem **24/24 testes passando**. Além dos codecs/StormLib/viewer já cobertos pelas versões anteriores, há regressões específicas para:

- resolução `custom -> base rawcode -> SLK nativo`;
- preservação de override sem “materializar” valores herdados;
- leitura de valores de Profile (`Art`, `Buttonpos`);
- parser SYLK esparso;
- campos Reforged `uabs` / `uhas`;
- Object Data v3 e modification sets.

## v3.7.0 — navegador e idiomas

- barra de rolagem vertical e horizontal visível no navegador da esquerda;
- clique em **Objeto**, **Base** ou **Tabela** para ordenar; clique de novo para inverter crescente/decrescente;
- setas ▲/▼ mostram a ordenação ativa;
- troca de idioma em tempo real por botões com bandeiras **Brasil (PT)** e **Estados Unidos (EN)**;
- combos, botões, abas, campos principais, viewer e balões de ajuda acompanham o idioma;
- a troca de idioma preserva o texto que ainda estiver sendo editado no formulário.

## v3.8.0 — CASC visual completo + abilities/doodads nativos

- Corrige modelos Reforged pretos: referências lógicas `.tif`/`.blp` agora tentam o payload `.dds` correspondente no CASC.
- O viewer associa `.tif`, `.blp`, `.tga` e `.dds` pelo mesmo nome-base ao aplicar texturas.
- AbilityData.slk passa a preencher levels, cooldown, mana cost, range, area e outros valores das abilities padrão.
- `*AbilityStrings.txt` é lido dos namespaces de locale (`ptbr.w3mod` / `enus.w3mod`) para recuperar nomes/tooltips/ícones nativos.
- Doodads padrão vêm de `Doodads\Doodads.slk` e aparecem junto dos custom.
- Corrigido o raw field do modelo de doodad para `dfil`, permitindo preview de doodads custom e nativos.
- Assets continuam sendo lidos somente em modo read-only do CASC/MPQ.

## Visual map placement (v1.0)

The **UNIDADES NO MAPA / UNITS ON MAP** workspace now includes a **MAP / MAPA** tab inspired by Jaccouille's War3AssetsImporter placement preview:

- reads `war3mapMap.blp` (with TGA/preview fallback) directly from the map;
- reads `war3map.w3e` bounds so canvas clicks map to real Warcraft world X/Y coordinates;
- overlays every `war3mapUnits.doo` placement as a facing-aware, owner-colored marker;
- clicking a marker selects the same placement in the left list;
- **+ ADICIONAR UNIDADE / + ADD UNIT → POSICIONAR NO MAPA / PLACE ON MAP** enters crosshair placement mode;
- clicking the terrain creates the placement in memory; **SALVAR / SAVE** remains the explicit write step;
- when the minimap artwork is missing/corrupt, a coordinate grid remains available instead of disabling placement.

This UI borrows the workflow idea from **War3AssetsImporter** while continuing to use Object Studio's own MPQ, object-data, Units.doo and minimal-JASS synchronization code.

The integrated asset importer also offers two placement strategies when **Import + create + add to map** is selected:

- **Visual — click the map** (default from the Units on Map workflow): imports/creates the unit types first, then switches to the MAP tab and asks for one terrain click per created model.
- **Automatic X/Y grid**: preserves the previous batch-grid workflow for very large model sets.
