# Copyright 2018-20 ForgeFlow S.L.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockRule(models.Model):
    _inherit = "stock.rule"

    def _prepare_mrp_production_request(
        self,
        product_id,
        product_qty,
        product_uom,
        location_id,
        name,
        origin,
        company_id,
        values,
        bom,
    ):
        data = self._prepare_mo_vals(
            product_id,
            product_qty,
            product_uom,
            location_id,
            name,
            origin,
            company_id,
            values,
            bom,
        )
        # Campos da ordem de produção que não existem no pedido de produção
        data["date_planned_start"] = data.get("date_start") or fields.Datetime.now()
        data["date_planned_finished"] = (
            data.get("date_deadline") or data["date_planned_start"]
        )
        data["propagate"] = data.get("propagate_cancel", False)
        for key in [
            "date_start",
            "date_deadline",
            "propagate_cancel",
            "user_id",
            "name",
            "location_final_id",
            "product_description_variants",
            "never_product_template_attribute_value_ids",
            "procurement_group_id",
        ]:
            data.pop(key, None)
        picking_type = self.env["stock.picking.type"].browse(
            data.get("picking_type_id")
        )
        fallback_loc = picking_type.warehouse_id.lot_stock_id
        for key in ("location_src_id", "location_dest_id"):
            if not data.get(key):
                data[key] = fallback_loc.id
        data["state"] = "to_approve"
        # outros módulos acrescentam chaves à ordem de produção (ex.: is_sub_mo)
        # que não existem no pedido de produção
        request_fields = self.env["mrp.production.request"]._fields
        data = {key: value for key, value in data.items() if key in request_fields}
        orderpoint = values.get("orderpoint_id")
        if orderpoint:
            data["orderpoint_id"] = orderpoint.id
        procurement_group = values.get("group_id")
        if procurement_group:
            data["procurement_group_id"] = procurement_group.id
        return data

    def _need_production_request(self, product_id, action="manufacture"):
        return action == "manufacture" and product_id.mrp_production_request

    def _run_production_request(
        self,
        product_id,
        product_qty,
        product_uom,
        location_id,
        name,
        origin,
        values,
        company_id,
    ):
        """Trying to handle this as much similar as possible to Odoo
        production orders. See `_run_manufacture` in Odoo standard."""
        request_obj = self.env["mrp.production.request"]
        request_obj_sudo = request_obj.sudo().with_company(company_id)
        bom = self._get_matching_bom(product_id, company_id, values)
        if not bom:
            raise UserError(
                _(
                    "There is no Bill of Material found for the product %s. "
                    "Please define a Bill of Material for this product."
                )
                % (product_id.display_name,)
            )

        # create the MR as SUPERUSER because the current user may not
        # have the rights to do it (mto product launched by a sale for example)
        request = request_obj_sudo.create(
            self._prepare_mrp_production_request(
                product_id,
                product_qty,
                product_uom,
                location_id,
                name,
                origin,
                company_id,
                values,
                bom,
            )
        )
        origin_production = (
            values.get("move_dest_ids")
            and values["move_dest_ids"][0].raw_material_production_id
            or False
        )
        orderpoint = values.get("orderpoint_id")
        if orderpoint:
            request.message_post_with_source(
                "mail.message_origin_link",
                render_values={"self": request, "origin": orderpoint},
                subtype_id=self.env.ref("mail.mt_note").id,
            )
        if origin_production:
            request.message_post_with_source(
                "mail.message_origin_link",
                render_values={"self": request, "origin": origin_production},
                subtype_id=self.env.ref("mail.mt_note").id,
            )
        return True

    @api.model
    def _run_manufacture(self, procurements):
        no_mr_procs = []
        for procurement, _rule in procurements:
            if self._need_production_request(procurement.product_id):
                self._run_production_request(
                    procurement.product_id,
                    procurement.product_qty,
                    procurement.product_uom,
                    procurement.location_id,
                    procurement.name,
                    procurement.origin,
                    procurement.values,
                    procurement.company_id,
                )
            else:
                no_mr_procs.append((procurement, _rule))

        return super()._run_manufacture(no_mr_procs)
