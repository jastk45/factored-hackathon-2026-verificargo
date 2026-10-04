"""Test del clasificador de intención: 128 mensajes escritos por el equipo.

Independiente del entrenamiento a propósito (D-11): el entrenamiento son
traducciones automáticas de BANKING77; esto se escribió a mano, en español de
MX/CO/AR y portugués de Brasil, con regionalismos, errores de tipeo, sin
tildes y algún caso de portuñol. Ninguna frase sale de BANKING77.

Origen: team-generated. Congelado junto con el eval set (tag eval-v1).

    uv run python eval/cases/intent_test_v1.py   # escribe el .jsonl
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_suffix(".jsonl")

CASES: dict[str, dict[str, list[str]]] = {
    "unrecognized_charge": {
        "es": [
            "Me aparece un cargo de 450 pesos que yo no hice",
            "Hay un cobro en mi tarjeta de un lugar que no conozco",
            "no reconozco una compra de ayer en mi estado de cuenta",
            "Che, me figura un consumo en la tarjeta que no es mío",
            "Buenas, me salió un débito raro, yo no compré nada en ese comercio",
            "veo una transacción que no autoricé en mi tarjeta de crédito",
            "Qué es este cargo de NETFLX? yo no tengo esa suscripción",
            "tengo un cargo q no reconosco de 89.900",
        ],
        "pt": [
            "Apareceu uma cobrança no meu cartão que eu não fiz",
            "Não reconheço uma compra de ontem na fatura",
            "tem um débito estranho no meu cartão, não comprei nada nessa loja",
            "Olá, vi uma transação que não autorizei no cartão de crédito",
            "que cobrança é essa de 120 reais? não conheço essa empresa",
            "nao reconheco essa compra de 350",
            "Tem um gasto na minha fatura que não é meu",
            "Hola, tenho uma cobrança que no reconozco no cartão",
        ],
    },
    "duplicate_charge": {
        "es": [
            "Me cobraron dos veces la misma compra",
            "aparece el mismo cargo duplicado en mi tarjeta",
            "Pagué una vez en el súper y me lo cobraron doble",
            "che, me debitaron dos veces el mismo pago de la luz",
            "hay dos cargos iguales del mismo día, solo compré una vez",
            "me cobraron 3 veces el uber de anoche",
            "el cobro de mi suscripción salió repetido este mes",
            "me hicieron el cargo dos veces por el mismo pedido",
        ],
        "pt": [
            "Fui cobrado duas vezes pela mesma compra",
            "a mesma cobrança apareceu duplicada no cartão",
            "paguei uma vez só e cobraram em dobro",
            "tem duas cobranças iguais no mesmo dia",
            "cobraram três vezes a corrida do uber",
            "minha assinatura veio cobrada repetida esse mês",
            "debitaram duas vezes o mesmo pagamento",
            "a compra do mercado saiu duplicada na fatura",
        ],
    },
    "wrong_amount": {
        "es": [
            "Compré algo de 200 pesos y me cobraron 2000",
            "el monto que me cobraron no es el que pagué en la tienda",
            "me cobraron más de lo que decía el ticket",
            "el tipo de cambio que me aplicaron está mal, me cobraron de más",
            "pagué 50 mil y en el extracto aparecen 80 mil",
            "me cobraron una comisión que nadie me avisó",
            "el cajero me dio menos plata de la que me descontaron",
            "el cargo es mayor al precio acordado con el comercio",
        ],
        "pt": [
            "comprei algo de 100 reais e cobraram 1000",
            "o valor cobrado não é o que paguei na loja",
            "cobraram mais do que estava no recibo",
            "a taxa de câmbio aplicada está errada, cobraram a mais",
            "o caixa eletrônico me deu menos dinheiro do que foi debitado",
            "cobraram uma tarifa que ninguém me avisou",
            "paguei 50 e na fatura aparece 80",
            "o valor da cobrança é maior que o combinado",
        ],
    },
    "merchandise_not_received": {
        "es": [
            "Pagué un pedido en línea y nunca me llegó",
            "compré unos zapatos y no recibí nada",
            "pedí un reembolso hace semanas y todavía no aparece",
            "la tienda me canceló la compra pero no me devolvieron la plata",
            "quiero que me devuelvan el dinero de una compra que no llegó",
            "pagué un servicio que nunca me prestaron",
            "el vendedor no me mandó el producto y ya me cobraron",
            "quiero pedir el reembolso de una compra",
        ],
        "pt": [
            "paguei um pedido online e nunca chegou",
            "comprei um tênis e não recebi nada",
            "pedi reembolso há semanas e ainda não apareceu",
            "a loja cancelou a compra mas não devolveu o dinheiro",
            "quero meu dinheiro de volta de uma compra que não chegou",
            "paguei por um serviço que nunca foi prestado",
            "o vendedor não enviou o produto e já me cobraram",
            "quero solicitar o estorno de uma compra",
        ],
    },
    "card_lost_stolen": {
        "es": [
            "Me robaron la cartera con la tarjeta adentro",
            "perdí mi tarjeta de débito",
            "creo que clonaron mi tarjeta",
            "no encuentro mi tarjeta desde ayer",
            "me asaltaron y se llevaron mis tarjetas",
            "alguien tiene los datos de mi tarjeta, me llegó un SMS de una compra",
            "se me cayó la tarjeta en el taxi",
            "me robaron el celular que tenía la tarjeta vinculada",
        ],
        "pt": [
            "roubaram minha carteira com o cartão dentro",
            "perdi meu cartão de débito",
            "acho que clonaram meu cartão",
            "não encontro meu cartão desde ontem",
            "fui assaltado e levaram meus cartões",
            "alguém está com os dados do meu cartão",
            "esqueci o cartão no táxi",
            "roubaram meu celular com o cartão cadastrado",
        ],
    },
    "dispute_status": {
        "es": [
            "¿cómo va mi reclamo del cargo que no reconocí?",
            "quiero saber el estado de mi disputa",
            "hace una semana abrí un caso, ¿ya lo resolvieron?",
            "¿ya me devolvieron el dinero del reclamo?",
            "mi pago aparece pendiente desde hace días",
            "¿en qué estado está mi aclaración?",
            "la transferencia sigue pendiente, ¿qué pasa?",
            "quería consultar mi número de caso",
        ],
        "pt": [
            "como está minha contestação da cobrança?",
            "quero saber o status da minha disputa",
            "abri um chamado semana passada, já resolveram?",
            "já devolveram o dinheiro da reclamação?",
            "meu pagamento está pendente há dias",
            "em que pé está minha contestação?",
            "a transferência continua pendente, o que houve?",
            "queria consultar o número do meu protocolo",
        ],
    },
    "policy_question": {
        "es": [
            "¿cuánto tiempo tengo para reclamar un cargo?",
            "¿cuántos días tarda el banco en responder una disputa?",
            "¿qué necesito para disputar un cargo?",
            "¿por qué me rechazaron la compra con la tarjeta?",
            "¿me devuelven el dinero mientras investigan?",
            "¿cuál es el procedimiento para desconocer una compra?",
            "¿por qué no pude sacar plata del cajero?",
            "¿puedo cancelar una transferencia que ya hice?",
        ],
        "pt": [
            "quanto tempo eu tenho para contestar uma cobrança?",
            "em quantos dias o banco responde uma contestação?",
            "o que preciso para contestar uma compra?",
            "por que minha compra no cartão foi recusada?",
            "vocês devolvem o dinheiro enquanto investigam?",
            "qual é o procedimento para contestar uma compra?",
            "por que não consegui sacar no caixa eletrônico?",
            "posso cancelar uma transferência que já fiz?",
        ],
    },
    "out_of_scope": {
        "es": [
            "quiero abrir una cuenta de inversión en criptomonedas",
            "¿cuál es el horario de la sucursal del centro?",
            "quiero cambiar mi número de celular registrado",
            "¿me pueden subir el límite de crédito?",
            "¿cómo activo mi tarjeta nueva?",
            "quiero pedir un préstamo personal",
            "¿qué clima va a hacer mañana?",
            "olvidé mi PIN, ¿cómo lo cambio?",
        ],
        "pt": [
            "quero abrir uma conta de investimento em cripto",
            "qual o horário da agência do centro?",
            "quero mudar meu número de celular cadastrado",
            "vocês podem aumentar meu limite?",
            "como ativo meu cartão novo?",
            "quero pedir um empréstimo pessoal",
            "como vai estar o tempo amanhã?",
            "esqueci minha senha, como troco?",
        ],
    },
}


def rows() -> list[dict]:
    out = []
    for intent, by_lang in CASES.items():
        for lang, texts in by_lang.items():
            for i, text in enumerate(texts, start=1):
                out.append({
                    "case_id": f"INT-{lang.upper()}-{intent[:6].upper()}-{i:02d}",
                    "text": text,
                    "intent": intent,
                    "language": lang,
                    "origin": "team-generated",
                })
    return out


if __name__ == "__main__":
    data = rows()
    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n",
        encoding="utf-8",
    )
    print(f"{len(data)} casos -> {OUT.name}")
