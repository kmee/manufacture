# Copyright 2017-18 ForgeFlow S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestMrpProductionRequest(TransactionCase):
    def setUp(self, *args, **kwargs):
        super().setUp(*args, **kwargs)
        self.production_model = self.env["mrp.production"]
        self.request_model = self.env["mrp.production.request"]
        self.wiz_model = self.env["mrp.production.request.create.mo"]
        self.bom_model = self.env["mrp.bom"]
        self.group_model = self.env["procurement.group"]
        self.product_model = self.env["product.product"]
        self.bom_model = self.env["mrp.bom"]
        self.boml_model = self.env["mrp.bom.line"]

        self.warehouse = self.env.ref("stock.warehouse0")
        self.stock_loc = self.env.ref("stock.stock_location_stock")
        route_manuf = self.env.ref("mrp.route_warehouse0_manufacture")

        # Prepare Products:
        self.product = self.product_model.create(
            {
                "name": "Test Product",
                "is_storable": True,
                "mrp_production_request": True,
                "route_ids": [(6, 0, route_manuf.ids)],
            }
        )

        self.product_no_bom = self.product_model.create(
            {
                "name": "Test Product without BoM",
                "is_storable": True,
                "mrp_production_request": True,
                "route_ids": [(6, 0, route_manuf.ids)],
            }
        )
        self.product_orderpoint = self.product_model.create(
            {
                "name": "Test Product for orderpoint",
                "is_storable": True,
                "mrp_production_request": True,
                "route_ids": [(6, 0, route_manuf.ids)],
            }
        )
        product_component = self.product_model.create(
            {
                "name": "Test component",
                "is_storable": True,
                "mrp_production_request": True,
                "route_ids": [(6, 0, route_manuf.ids)],
            }
        )

        # Create Bill of Materials:
        self.test_bom_1 = self.bom_model.create(
            {
                "product_id": self.product_orderpoint.id,
                "product_tmpl_id": self.product_orderpoint.product_tmpl_id.id,
                "product_uom_id": self.product_orderpoint.uom_id.id,
                "product_qty": 1.0,
                "type": "normal",
            }
        )
        self.boml_model.create(
            {
                "bom_id": self.test_bom_1.id,
                "product_id": product_component.id,
                "product_qty": 1.0,
            }
        )

        # BoM for the test product
        self.test_bom_0 = self.bom_model.create(
            {
                "product_id": self.product.id,
                "product_tmpl_id": self.product.product_tmpl_id.id,
                "product_uom_id": self.product.uom_id.id,
                "product_qty": 1.0,
                "type": "normal",
                "bom_line_ids": [
                    (0, 0, {"product_id": product_component.id, "product_qty": 2.0})
                ],
            }
        )

        # Create Orderpoint:
        self.orderpoint = self.env["stock.warehouse.orderpoint"].create(
            {
                "warehouse_id": self.warehouse.id,
                "location_id": self.warehouse.lot_stock_id.id,
                "product_id": self.product_orderpoint.id,
                "product_min_qty": 10.0,
                "product_max_qty": 50.0,
                "product_uom": self.product_orderpoint.uom_id.id,
            }
        )

        # Create Procurement Group:
        self.test_group = self.group_model.create({"name": "TEST"})

        # Create User:
        self.test_user = self.env["res.users"].create({"name": "John", "login": "test"})

    def procure(self, group, product, qty=4.0):
        warehouse = self.warehouse
        values = {
            "date_planned": fields.Datetime.now(),
            "group_id": group,
            "warehouse_id": warehouse,
        }
        self.group_model.run(
            [
                self.test_group.Procurement(
                    product,
                    qty,
                    product.uom_id,
                    self.stock_loc,
                    group.name,
                    group.name,
                    warehouse.company_id,
                    values,
                )
            ]
        )
        return True

    def test_01_manufacture_request(self):
        """Tests manufacture request workflow."""
        self.procure(self.test_group, self.product)
        request = self.request_model.search(
            [
                ("product_id", "=", self.product.id),
                ("procurement_group_id", "=", self.test_group.id),
            ]
        )
        self.assertEqual(len(request), 1)
        if "assigned_to_ids" in request._fields:
            # with an approval extension installed, only approvers can approve
            admin = self.env.ref("base.user_admin")
            admin.groups_id |= self.env.ref(
                "mrp_production_request.group_mrp_production_request_manager"
            )
            request.assigned_to_ids = admin
            request = request.with_user(admin)
        request.button_to_approve()
        request.button_draft()
        request.button_to_approve()
        request.button_approved()
        self.assertEqual(request.pending_qty, 4.0)
        wiz = self.wiz_model.with_context(
            active_ids=request.ids,
            active_id=request.id,
            active_model="mrp.production.request",
        ).create({})
        wiz.compute_product_line_ids()
        wiz.mo_qty = 4.0
        wiz.create_mo()
        mo = self.production_model.search(
            [("mrp_production_request_id", "=", request.id)]
        )
        self.assertTrue(mo, "No MO created.")
        self.assertEqual(request.mrp_production_count, 1)
        self.assertEqual(mo.product_qty, 4.0)
        self.assertEqual(mo.date_start, request.date_planned_start)
        self.assertEqual(sum(mo.move_raw_ids.mapped("product_uom_qty")), 8.0)
        self.assertEqual(request.pending_qty, 4.0)
        mo.action_confirm()
        self.assertEqual(request.manufactured_qty, 4.0)
        self.assertEqual(request.pending_qty, 0.0)
        request.button_done()

    def test_02_assignation(self):
        """Tests assignation of manufacturing requests."""
        randon_bom_id = self.bom_model.search([], limit=1).id
        request = self.request_model.create(
            {
                "assigned_to": self.test_user.id,
                "product_id": self.product.id,
                "product_qty": 5.0,
                "bom_id": randon_bom_id,
            }
        )
        request._onchange_product_id()
        self.assertEqual(request.product_uom_id, self.product.uom_id)
        self.assertEqual(
            request.bom_id.product_tmpl_id,
            self.product.product_tmpl_id,
            "Wrong Bill of Materials.",
        )
        request.write({"assigned_to": self.uid})
        self.assertTrue(request.message_follower_ids, "Followers not added correctly.")

    def test_03_substract_qty_from_orderpoint(self):
        """Quantity in Manufacturing Requests should be considered by
        orderpoints."""
        request = self.request_model.search(
            [("product_id", "=", self.product_orderpoint.id)]
        )
        self.assertFalse(request)
        # self.env['procurement.group'].run_scheduler()
        with mute_logger("odoo.addons.stock.models.stock_rule"):
            self.env["procurement.group"].run_scheduler()
        request = self.request_model.search(
            [("product_id", "=", self.product_orderpoint.id)]
        )
        self.assertEqual(len(request), 1)
        # Running again the scheduler should not generate a new MR.
        # self.env['procurement.group'].run_scheduler()
        with mute_logger("odoo.addons.stock.models.stock_rule"):
            self.env["procurement.group"].run_scheduler()
        request = self.request_model.search(
            [("product_id", "=", self.product_orderpoint.id)]
        )
        self.assertEqual(len(request), 1)

    def test_04_raise_errors(self):
        """Tests user errors raising properly."""
        with self.assertRaises(UserError):
            # No Bill of Materials:
            self.procure(self.test_group, self.product_no_bom)

    def test_wizards_open_for_regular_user(self):
        user = self.env["res.users"].create(
            {
                "name": "Wizard user",
                "login": "wizard_user_access",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref(
                                "mrp_production_request.group_mrp_production_request_user"
                            ).id
                        ],
                    )
                ],
            }
        )
        for model in (
            "mrp.production.request.create.mo",
            "mrp.production.request.create.mo.line",
        ):
            self.env[model].with_user(user).check_access("create")
