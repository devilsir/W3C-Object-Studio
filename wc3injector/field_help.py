from __future__ import annotations

import re

# Explanations are written for the map maker, not for the binary format.
# The raw metadata is appended separately so users can still see the field id,
# source SLK/profile and min/max limits.

EXACT = {
    # Text / identity
    "unam": "Define o nome exibido da unidade/herói no jogo, em seleção, tooltips e outras telas que usam o nome do objeto.",
    "unsf": "Adiciona um sufixo só para organizar e diferenciar o objeto dentro do editor. Normalmente não aparece para o jogador durante a partida.",
    "upro": "Lista os nomes próprios que um herói pode receber ao entrar no jogo. Os nomes são separados por vírgula.",
    "upru": "Define quantos nomes da lista de Nomes Próprios entram no sorteio do herói. Ex.: com 10 nomes cadastrados e valor 3, o jogo usa apenas os 3 primeiros como opções aleatórias.",
    "utyp": "Define as classificações da unidade, como Ancient, Mechanical, Peon, Town Hall etc. Essas classificações podem mudar quais habilidades, alvos e regras do jogo afetam a unidade.",
    "utip": "Texto curto mostrado como título/tooltip da unidade em partes da interface.",
    "utub": "Descrição longa mostrada ao jogador no tooltip da unidade.",

    # Abilities on units
    "uabi": "Lista as habilidades normais que a unidade possui. Alterar aqui adiciona/remove botões, passivas e comportamentos ligados a essas abilities.",
    "uhab": "Lista as habilidades de herói disponíveis para aprender. Elas aparecem no menu de habilidades do herói conforme as regras de nível da spell.",
    "uabs": "Lista de habilidades normais usada pela skin da unidade. É a variante de habilidades ligada ao sistema de skins do Reforged.",
    "uhas": "Lista de habilidades de herói usada pela skin da unidade no Reforged.",

    # Art
    "umdl": "Escolhe o arquivo MDX/MDL usado para desenhar a unidade no mapa. Trocar este valor muda o modelo 3D visível no jogo.",
    "uico": "Escolhe o ícone principal da unidade/herói mostrado na interface, por exemplo em seleção, treino e menus que usam o ícone do objeto.",
    "usca": "Multiplica o tamanho visual do modelo 3D. 1.00 é o tamanho original; 2.00 dobra o tamanho visual. Não aumenta colisão nem alcance sozinho.",
    "ussc": "Altera o tamanho do círculo de seleção mostrado no chão. É visual/interativo e não muda automaticamente a colisão física.",
    "uclr": "Controla a quantidade de vermelho aplicada ao modelo da unidade. Junto com Green/Blue altera a coloração visual do modelo.",
    "uclg": "Controla a quantidade de verde aplicada ao modelo da unidade. Junto com Red/Blue altera a coloração visual do modelo.",
    "uclb": "Controla a quantidade de azul aplicada ao modelo da unidade. Junto com Red/Green altera a coloração visual do modelo.",

    # Stats
    "uhpm": "Define a vida máxima da unidade. Quanto maior, mais dano ela aguenta antes de morrer.",
    "umpm": "Define a mana máxima disponível para usar habilidades.",
    "umpi": "Define com quanta mana a unidade nasce/entra no mapa.",
    "uhpr": "Define quantos pontos de vida a unidade regenera por segundo, conforme o tipo de regeneração configurado.",
    "umpr": "Define quantos pontos de mana a unidade regenera por segundo.",
    "usid": "Define até onde a unidade enxerga durante o dia.",
    "usin": "Define até onde a unidade enxerga durante a noite.",
    "uacq": "Define a distância em que a unidade procura/adquire automaticamente inimigos para atacar. Não é necessariamente igual ao alcance do ataque.",
    "ufoo": "Define quanto de comida/suprimento esta unidade consome.",
    "ufma": "Define quanto de comida/suprimento esta unidade fornece ao jogador.",
    "ugol": "Custo em ouro para produzir/comprar a unidade.",
    "ulum": "Custo em madeira para produzir/comprar a unidade.",
    "ubld": "Tempo necessário para treinar/construir a unidade.",

    # Movement
    "umvs": "Define a velocidade máxima de deslocamento da unidade pelo mapa.",
    "umvt": "Define o tipo de movimento, como terrestre, voador, flutuante etc. Isso afeta por onde a unidade pode andar e como o pathing é tratado.",
    "umvh": "Define a altura visual/base de movimento da unidade. Em unidades voadoras, controla quão alto o modelo fica acima do chão.",
    "umvr": "Define a rapidez com que a unidade consegue virar para outra direção. Valores maiores deixam a rotação mais rápida.",
    "ucol": "Define o raio de colisão/pathing da unidade. Valores maiores fazem ela ocupar mais espaço e dificultam a passagem de outras unidades.",

    # Combat
    "udef": "Define a armadura base da unidade antes de bônus, itens, buffs e upgrades.",
    "uarm": "Define o tipo visual/sonoro de armadura usado por alguns efeitos e sons de impacto.",
    "udty": "Define o tipo de defesa. Isso participa da tabela de multiplicadores de dano do Warcraft contra tipos de ataque.",
    "ua1b": "Dano base do Ataque 1 antes da rolagem dos dados de dano e bônus adicionais.",
    "ua1d": "Quantidade de dados rolados pelo Ataque 1. O dano aleatório é calculado junto com Lados por dado.",
    "ua1s": "Número de lados de cada dado de dano do Ataque 1. Ex.: 2 dados de 6 lados adicionam 2–12 de dano aleatório.",
    "ua1c": "Intervalo, em segundos, entre ataques do Ataque 1. Menor valor = ataca mais rápido.",
    "ua1r": "Distância máxima em que o Ataque 1 pode atingir o alvo.",
    "ua1m": "Modelo do projétil disparado pelo Ataque 1, quando o tipo de arma usa projétil.",
    "ua1t": "Tipo de ataque do Ataque 1. Participa dos multiplicadores de dano contra os diferentes tipos de defesa.",
    "ua1w": "Define como o Ataque 1 funciona fisicamente, por exemplo melee, missile, artillery etc. Isso altera projétil, splash e comportamento do ataque.",
    "ua1g": "Define quais categorias de alvo o Ataque 1 pode atingir, como ar, chão, inimigos, estruturas etc.",

    # Hero
    "upra": "Escolhe o atributo primário do herói. Esse atributo influencia o dano básico do herói e qual stat é destacado como principal.",
    "ulev": "Define Stats - Level do TIPO da unidade. Não define o nível inicial de uma instância de herói já colocada no mapa. Para alterar o nível que o herói realmente terá ao iniciar a partida, use UNIDADES NO MAPA > Nível do herói; o Studio sincroniza essa mudança no runtime JASS quando necessário.",
    "ustr": "Força base do herói no nível inicial. Força normalmente aumenta vida e regeneração, além de dano se for atributo primário.",
    "uagi": "Agilidade base do herói. Agilidade normalmente influencia armadura e velocidade de ataque, além de dano se for atributo primário.",
    "uint": "Inteligência base do herói. Inteligência normalmente influencia mana e regeneração de mana, além de dano se for atributo primário.",
    "ustp": "Quanto de Força o herói ganha automaticamente a cada nível.",
    "uagp": "Quanto de Agilidade o herói ganha automaticamente a cada nível.",
    "uinp": "Quanto de Inteligência o herói ganha automaticamente a cada nível.",

    # Ability core
    "anam": "Nome da habilidade mostrado ao jogador e no editor.",
    "ansf": "Sufixo usado para organizar a habilidade no editor. Em geral não aparece durante a partida.",
    "aart": "Ícone da habilidade mostrado no command card e em outros elementos da interface.",
    "alev": "Quantidade de níveis que a habilidade possui. Isso define quantas versões de dados por nível podem ser configuradas.",
    "arlv": "Nível mínimo do herói necessário para aprender o primeiro nível desta habilidade.",
    "alsk": "Quantidade de níveis de herói que precisam passar entre um nível da habilidade e o próximo.",
    "aher": "Define se a ability é tratada como habilidade de herói, podendo participar do sistema de aprendizado por níveis.",
    "aite": "Define se a ability é tratada como habilidade de item.",
    "arac": "Raça associada à habilidade para organização/interface e alguns comportamentos do editor.",
    "abpx": "Coluna do botão da habilidade no command card. Valores diferentes movem o botão horizontalmente.",
    "abpy": "Linha do botão da habilidade no command card. Valores diferentes movem o botão verticalmente.",
    "ahky": "Tecla de atalho usada para ativar a habilidade quando o botão está disponível.",
    "atp1": "Texto curto do tooltip da habilidade neste nível.",
    "aub1": "Descrição detalhada mostrada no tooltip da habilidade neste nível.",
    "aret": "Texto curto mostrado na interface de pesquisa/aprendizado da habilidade.",
    "arut": "Descrição detalhada mostrada na interface de pesquisa/aprendizado da habilidade.",
    "arhk": "Tecla de atalho usada no botão de pesquisar/aprender a habilidade.",
    "acdn": "Tempo de recarga da habilidade neste nível. Durante esse tempo ela não pode ser usada novamente.",
    "amcs": "Quantidade de mana consumida ao usar a habilidade neste nível.",
    "aran": "Distância máxima entre o lançador e o alvo/ponto ao iniciar a habilidade.",
    "aare": "Raio/área de efeito da habilidade neste nível. O significado exato depende da habilidade-base.",
    "adur": "Duração do efeito contra unidades normais neste nível.",
    "ahdu": "Duração do efeito contra heróis neste nível.",
    "atar": "Define quais tipos de alvo a habilidade aceita, como inimigos, aliados, ar, chão, heróis, estruturas etc.",
    "aord": "Order ID/string usada pela engine para emitir a ordem desta habilidade. Duplicar orders incompatíveis pode causar conflito entre skills.",
    "acat": "Modelo/efeito visual criado no lançador quando a habilidade é usada.",
    "atat": "Modelo/efeito visual criado no alvo da habilidade.",
    "asat": "Modelo/efeito visual especial associado à habilidade.",
    "aeat": "Modelo/efeito visual de efeito/impacto associado à habilidade.",
    "aaea": "Modelo/efeito visual usado para representar a área da habilidade.",
    "amat": "Modelo usado como míssil/projétil da habilidade.",
    "aani": "Nome(s) de animação que o lançador tenta reproduzir ao usar a habilidade, como spell, slam ou channel.",

    # Doodad
    "dnam": "Nome do doodad no editor.",
    "dmod": "Modelo MDX/MDL usado para desenhar o doodad no mapa.",
    "dmis": "Menor escala aleatória permitida ao posicionar o doodad.",
    "dmas": "Maior escala aleatória permitida ao posicionar o doodad.",
    "dvar": "Quantidade de variações visuais/modelos que o doodad pode usar.",
    "dsel": "Define se o doodad pode ser selecionado/interagido no editor conforme o comportamento desse tipo.",
}


def _clean_label(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip())


def _generic_description(field_id: str, label: str, category: str) -> str:
    l = _clean_label(label)
    low = l.casefold()
    # Strip the category prefix the metadata often embeds in the display label.
    core = re.sub(r"^(art|combat|editor|hero|movement|move|pathing|sound|stats|text|abilities|ability|tech|geral)\s*-\s*", "", l, flags=re.I)
    clow = core.casefold()

    patterns = [
        (("cast backswing",), "Tempo depois do ponto de lançamento em que a unidade ainda fica presa à animação de conjuração. Valores menores deixam o herói livre mais cedo após soltar a habilidade."),
        (("cast point",), "Momento, em segundos, dentro da animação em que a habilidade realmente dispara. Antes desse ponto, cancelar a ordem normalmente cancela o cast."),
        (("animation - run speed", "run speed"), "Velocidade de referência usada para sincronizar a animação de corrida com o movimento da unidade."),
        (("animation - walk speed", "walk speed"), "Velocidade de referência usada para sincronizar a animação de caminhada com o movimento da unidade."),
        (("animation - blend time", "blend time"), "Tempo usado para misturar suavemente a transição entre animações. Valores maiores deixam as trocas mais suaves e lentas."),
        (("death time",), "Tempo que a unidade permanece na animação/corpo de morte antes da próxima etapa de remoção/decomposição."),
        (("elevation - sample points", "sample points"), "Quantidade de pontos usados para ajustar o modelo à inclinação do terreno. Mais pontos podem deixar a adaptação ao chão mais precisa."),
        (("elevation - sample radius", "sample radius"), "Raio ao redor da unidade usado para amostrar a altura do terreno ao calcular inclinação/elevacão visual."),
        (("fog of war - sample radius", "fog of war"), "Raio usado pela representação da unidade na amostragem do fog of war. Afeta como a visibilidade é calculada ao redor do modelo."),
        (("water shadow",), "Liga/desliga a sombra/reflexo visual da unidade sobre água quando suportado pelo renderer."),
        (("maximum pitch angle", "pitch angle"), "Limite de inclinação para frente/trás do modelo ao acompanhar terrenos inclinados."),
        (("maximum roll angle", "roll angle"), "Limite de inclinação lateral do modelo ao acompanhar terrenos inclinados."),
        (("occluder height",), "Altura usada para oclusão: ajuda a engine a decidir quando elementos podem esconder/revelar partes do cenário em relação à unidade."),
        (("orientation interpolation",), "Controla como a rotação/orientação do modelo é interpolada durante mudanças de direção."),
        (("projectile launch - x",), "Desloca horizontalmente, no eixo X local, o ponto de onde o projétil sai do modelo."),
        (("projectile launch - y",), "Desloca horizontalmente, no eixo Y local, o ponto de onde o projétil sai do modelo."),
        (("projectile launch - z",), "Define a altura do ponto de onde o projétil sai do modelo."),
        (("projectile impact - z",), "Define a altura em que projéteis miram/atingem visualmente esta unidade."),
        (("required animation names - attachments",), "Nomes/tags de animação exigidos pelos attachments do modelo. A engine usa essas tags ao escolher variações de animação/visual."),
        (("required animation names",), "Tags de animação que a unidade pede ao modelo, como alternate, upgrade, defend etc. Elas mudam quais sequences/geosets podem ser usados."),
        (("required attachment link names",), "Define nomes de links/attachments esperados no modelo para efeitos e elementos anexados."),
        (("required bone names",), "Define nomes de bones que a engine espera encontrar no modelo para certos recursos visuais."),
        (("scale projectiles",), "Quando ativo, projéteis anexados/gerados pela unidade podem acompanhar a escala visual do modelo."),
        (("selection circle - height",), "Altura vertical do círculo de seleção em relação ao chão/modelo."),
        (("selection circle",), "Controla o tamanho/posição do círculo usado para selecionar a unidade no mapa."),
        (("button position",), "Define a posição do botão desta unidade/ação na grade da interface do Warcraft."),
        (("icon - game interface", "game interface"), "Ícone principal mostrado para esta unidade em elementos da interface do jogo."),
        (("icon - score screen", "score screen"), "Ícone usado para representar esta unidade em telas de placar/resultado quando aplicável."),
        (("portrait model file", "portrait"), "Modelo usado no retrato 3D da interface quando a unidade é selecionada."),
        (("model file - extra versions", "extra versions"), "Lista versões/variações adicionais do modelo que a engine pode usar para este objeto."),
        (("model file",), "Arquivo MDX/MDL que define a aparência 3D deste objeto no jogo."),
        (("team color",), "Controla como o modelo usa a cor do jogador/time nas partes preparadas para Team Color."),
        (("scaling value", "scale"), "Controla o tamanho visual do modelo. Isso não muda automaticamente colisão, alcance ou stats."),
        (("name count",), "Define quantas entradas da lista de nomes próprios ficam disponíveis para o sorteio do nome do herói."),
        (("proper name",), "Controla a lista/uso dos nomes próprios que podem aparecer no herói durante a partida."),
        (("acquisition",), "Controla a distância em que a unidade detecta e escolhe inimigos automaticamente para atacar."),
        (("sight",), "Controla a distância de visão/revelação da unidade no mapa."),
        (("collision",), "Controla o espaço físico/pathing ocupado pela unidade e como outras unidades conseguem passar ao redor dela."),
        (("turn rate",), "Controla a velocidade com que a unidade gira para encarar outra direção."),
        (("movement speed", "move speed"), "Controla a velocidade de deslocamento da unidade pelo mapa."),
        (("movement type",), "Define o tipo de locomoção e, portanto, por quais áreas/pathing a unidade pode se mover."),
        (("height",), "Controla a altura associada a este aspecto visual/movimento do objeto. O efeito exato depende do campo mostrado."),
        (("hit points", "hp"), "Controla a quantidade de vida relacionada a este objeto. Valores maiores aumentam a resistência antes da morte."),
        (("mana",), "Controla a quantidade/regeneração de mana relacionada a este objeto e, portanto, a capacidade de usar habilidades."),
        (("armor", "defense"), "Controla a defesa/armadura deste objeto e pode alterar quanto dano ele recebe conforme as regras do Warcraft."),
        (("damage",), "Controla parte do dano causado por este ataque. O resultado final também pode depender de dados, upgrades, buffs e tipo de ataque."),
        (("cooldown",), "Controla o intervalo antes de esta ação/ataque/habilidade poder acontecer novamente. Menor valor significa maior frequência."),
        (("range",), "Controla a distância máxima relacionada a este ataque, aquisição ou habilidade, conforme o campo."),
        (("targets allowed", "targets"), "Define quais tipos de unidades/objetos podem ser afetados, como ar, chão, aliados, inimigos, heróis ou estruturas."),
        (("sound",), "Escolhe/ajusta o conjunto de sons reproduzidos por este objeto durante ações do jogo."),
    ]
    for needles, desc in patterns:
        if any(n in clow for n in needles):
            return desc

    if "animation" in clow:
        return "Controla um parâmetro de animação do modelo. Alterar este valor muda como ou quando a animação é reproduzida visualmente durante a partida."
    if "attack" in clow:
        return "Controla um parâmetro do ataque desta unidade. O efeito pode alterar dano, frequência, alcance, projétil, alvos ou comportamento do ataque."
    if "hero" in clow:
        return "Controla um parâmetro específico de herói e pode afetar progressão, atributos, habilidades ou apresentação do herói no jogo."
    if category.casefold() in {"arte", "art"}:
        return "Controla a apresentação visual deste objeto no jogo: modelo, animação, ícone, escala, cor ou outro parâmetro gráfico relacionado."
    if category.casefold() in {"combate", "combat"}:
        return "Controla um aspecto de combate deste objeto. Alterar o valor pode mudar como ele ataca, recebe dano ou interage com alvos."
    if category.casefold() in {"movimento", "movement"}:
        return "Controla um aspecto do movimento/pathing deste objeto no mapa."
    if category.casefold() in {"habilidades", "abilities"}:
        return "Controla quais habilidades ou comportamentos de ability estão ligados a esta unidade/herói."
    if category.casefold() in {"stats"}:
        return "Controla um atributo numérico usado pela unidade durante a partida, como vida, mana, custo, visão ou outro stat."
    return f"Controla o parâmetro “{core}” deste objeto no Warcraft III. Alterar este valor muda o comportamento ou a apresentação correspondente durante a partida ou no editor."


def describe_field(fd) -> str:
    field_id = getattr(fd, "id", "") or ""
    label = getattr(fd, "label", "") or field_id
    category = getattr(fd, "category", "") or ""
    gameplay = EXACT.get(field_id) or _generic_description(field_id, label, category)
    technical = (getattr(fd, "help", "") or "").strip()
    native_table = (getattr(fd, "native_table", "") or "").strip()
    native_column = (getattr(fd, "native_column", "") or "").strip()
    details = []
    if technical:
        details.append(technical)
    elif native_table or native_column:
        details.append(f"{field_id} · {native_table}.{native_column}".strip(" ."))
    elif field_id:
        details.append(f"Field ID: {field_id}")
    if details:
        return gameplay + "\n\nTécnico: " + " · ".join(details)
    return gameplay
