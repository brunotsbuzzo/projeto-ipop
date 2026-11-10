"""Passo a passo do cadastro de UMA regra na tela (antigo RegraService)."""
from __future__ import annotations

import logging

from playwright.sync_api import Page

from ipop.models.regra import RegraBase
from ipop.pages.formulario_regra import CadastroDeRegraPage
from ipop.pages.lista_regras import ListaRegrasPage

logger = logging.getLogger(__name__)


class CadastroService:
    def __init__(self, page: Page, *, verificar_apos_salvar: bool = True):
        self.page = page
        self.lista = ListaRegrasPage(page)
        self.formulario = CadastroDeRegraPage(page)
        self.verificar_apos_salvar = verificar_apos_salvar

    def _nome_do_modelo(self, regra: RegraBase, id_modelo: int) -> str:
        """Acha o modelo na lista pelo ID exato e devolve o nome que o IPOP mostra."""
        try:
            self.lista.pesquisar_por_id(id_modelo)
            return self.lista.nome_da_regra(id_modelo)
        except Exception as erro:
            if not regra.nome_regra_modelo:
                raise RuntimeError(f"Regra-modelo {id_modelo} não encontrada na lista do IPOP.") from erro
            logger.warning("Modelo %s não achado pelo ID (%s); tentando pelo nome '%s'.",
                           id_modelo, erro, regra.nome_regra_modelo)
            self.lista.pesquisar_por_nome(regra.nome_regra_modelo)
            # Mesmo pesquisando pelo nome, a linha escolhida é a do ID exato
            # (a busca também traz as "Cópia de ..." desse modelo).
            return self.lista.nome_da_regra(id_modelo)

    def _copiar_modelo(self, regra: RegraBase) -> None:
        id_modelo = int(regra.id_regra_modelo)
        self.lista.mostrar_mais_linhas()
        nome_modelo = self._nome_do_modelo(regra, id_modelo)
        if not nome_modelo:
            raise RuntimeError(f"Não consegui ler o nome da regra-modelo {id_modelo} na lista do IPOP.")

        # O IPOP não deixa a cópia nova selecionada. Para abrir a cópia CERTA (e nunca
        # uma "Cópia de ..." antiga que já esteja na lista), anota as cópias que já
        # existem e, depois de copiar, abre a única que apareceu.
        copias_antes = self.lista.ids_das_copias(nome_modelo)
        self.lista.selecionar_por_id(id_modelo)
        self.lista.copiar_selecionada()
        logger.info("Modelo %s (%s) copiado.", id_modelo, nome_modelo)

        copias_depois = self.lista.ids_das_copias(nome_modelo)
        novas = copias_depois - copias_antes
        if len(novas) != 1:
            raise RuntimeError(
                f"Depois de copiar o modelo {id_modelo}, esperava 1 'Cópia de {nome_modelo}' nova "
                f"na lista e achei {len(novas)} ({', '.join(sorted(novas)) or 'nenhuma'}). "
                "Parei sem editar nada. Confira a lista no IPOP."
            )
        id_copia = int(novas.pop())
        logger.info("Cópia nova: ID %s.", id_copia)
        self.lista.selecionar_por_id(id_copia)
        self.lista.abrir_selecionada()
        self.formulario.nome_regra_input.wait_for(state="visible", timeout=30_000)

        # Trava de segurança: só edita se o que abriu for mesmo uma cópia.
        # Sem isso, um clique na linha errada renomearia uma regra existente.
        nome_aberto = self.formulario.nome_regra_input.input_value().strip()
        if not nome_aberto.casefold().startswith("cópia de"):
            raise RuntimeError(
                f"A regra aberta se chama {nome_aberto!r} e não é uma 'Cópia de ...'. "
                "Parei sem salvar para não alterar uma regra existente."
            )

    def cadastrar(self, regra: RegraBase) -> str:
        """Cadastra a regra. Devolve 'confirmada' ou 'salva_sem_confirmacao'."""
        self.lista.esperar_lista_pronta()
        if regra.id_regra_modelo:
            self._copiar_modelo(regra)
        else:
            self.lista.abrir_nova_regra()
            self.formulario.nome_regra_input.wait_for(state="visible", timeout=30_000)

        self.formulario.preencher_secao_regra(regra)
        for acao in regra.variaveis:
            self.formulario.preencher_arvore_de_decisao(acao)

        self.formulario.salvar_regra()
        self.lista.esperar_carregamento()
        self.formulario.fechar_pagina_de_cadastro()
        self.lista.esperar_lista_pronta()

        if self.verificar_apos_salvar:
            if self.lista.regra_existe(regra.nome_regra):
                return "confirmada"
            logger.warning("Regra '%s' salva, mas não apareceu na busca da lista.", regra.nome_regra)
            return "salva_sem_confirmacao"
        return "salva_sem_confirmacao"
