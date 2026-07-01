import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from bot.states import ShopStates

class TestShopGoods:

    async def test_shop_callback_handler(self, make_callback_query, fsm_context, item_factory):
        from bot.handlers.user.shop_and_goods import shop_callback_handler

        await item_factory(name="Widget", price=100)
        await item_factory(name="Gadget", price=200)

        call = make_callback_query(data="shop", user_id=600001)

        with patch('bot.handlers.user.shop_and_goods.lazy_paginated_keyboard', new_callable=AsyncMock) as mock_kb:
            mock_kb.return_value = MagicMock()
            await shop_callback_handler(call, fsm_context)

        call.message.edit_text.assert_called_once()
        text = call.message.edit_text.call_args[0][0]
        assert "choose" in text or "shop.goods.choose" in text
        state = await fsm_context.get_state()
        assert state == ShopStates.viewing_goods

    async def test_navigate_goods(self, make_callback_query, fsm_context, item_factory):
        from bot.handlers.user.shop_and_goods import navigate_goods

        for i in range(15):
            await item_factory(name=f"Item_{i}", price=10 + i)

        call = make_callback_query(data="gp_1", user_id=600002)

        # Set up paginator state in FSM
        await fsm_context.update_data(goods_paginator=None)

        with patch('bot.handlers.user.shop_and_goods.lazy_paginated_keyboard', new_callable=AsyncMock) as mock_kb:
            mock_kb.return_value = MagicMock()
            await navigate_goods(call, fsm_context)

        call.message.edit_text.assert_called_once()


class TestItemInfo:

    async def test_item_info_display(self, make_callback_query, fsm_context, item_factory):
        from bot.handlers.user.shop_and_goods import item_info_callback_handler

        await item_factory(name="InfoItem", price=250, values=[("val1", False)])

        call = make_callback_query(data="itm:0:0", user_id=600020)
        await fsm_context.update_data(
            goods_page_items=[{"name": "InfoItem"}],
        )

        with patch('bot.main.security_middleware', None):
            await item_info_callback_handler(call, fsm_context)

        call.message.edit_text.assert_called_once()

    async def test_item_info_invalid_index(self, make_callback_query, fsm_context):
        from bot.handlers.user.shop_and_goods import item_info_callback_handler

        call = make_callback_query(data="itm:10:0", user_id=600021)
        await fsm_context.update_data(goods_page_items=[{"name": "SomeItem"}])

        await item_info_callback_handler(call, fsm_context)

        call.answer.assert_called_once()

    async def test_item_info_not_found_in_db(self, make_callback_query, fsm_context):
        from bot.handlers.user.shop_and_goods import item_info_callback_handler

        call = make_callback_query(data="itm:0:0", user_id=600022)
        await fsm_context.update_data(
            goods_page_items=[{"name": "NonExistent"}],
        )

        await item_info_callback_handler(call, fsm_context)

        call.answer.assert_called_once()

    async def test_item_info_unlimited_quantity(self, make_callback_query, fsm_context, item_factory):
        from bot.handlers.user.shop_and_goods import item_info_callback_handler

        await item_factory(name="InfItem", price=50, values=[("unlimited_val", True)])

        call = make_callback_query(data="itm:0:0", user_id=600023)
        await fsm_context.update_data(
            goods_page_items=[{"name": "InfItem"}],
        )

        with patch('bot.main.security_middleware', None):
            await item_info_callback_handler(call, fsm_context)

        call.message.edit_text.assert_called_once()
        text = call.message.edit_text.call_args[0][0]
        assert "quantity_unlimited" in text or "unlimited" in text.lower()


class TestBoughtItems:

    async def test_bought_items_empty(self, make_callback_query, fsm_context, user_factory):
        from bot.handlers.user.shop_and_goods import bought_items_callback_handler

        await user_factory(telegram_id=600030)

        call = make_callback_query(data="bought_items", user_id=600030)

        with patch('bot.handlers.user.shop_and_goods.lazy_paginated_keyboard', new_callable=AsyncMock) as mock_kb:
            mock_kb.return_value = MagicMock()
            await bought_items_callback_handler(call, fsm_context)

        call.message.edit_text.assert_called_once()
        text = call.message.edit_text.call_args[0][0]
        assert isinstance(text, str)

    async def test_bought_item_info_not_found(self, make_callback_query):
        from bot.handlers.user.shop_and_goods import bought_item_info_callback_handler

        call = make_callback_query(data="bought-item:99999:profile", user_id=600031)

        await bought_item_info_callback_handler(call)

        call.answer.assert_called_once()
