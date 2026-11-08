/*
 *  yosys -- Yosys Open SYnthesis Suite
 *
 *  Copyright (C) 2012  Claire Xenia Wolf <claire@yosyshq.com>
 *                2019  Eddie Hung    <eddie@fpgeh.com>
 *
 *  Permission to use, copy, modify, and/or distribute this software for any
 *  purpose with or without fee is hereby granted, provided that the above
 *  copyright notice and this permission notice appear in all copies.
 *
 *  THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 *  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 *  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 *  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 *  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 *  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 *  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 *
 */

#include "kernel/yosys.h"
#include "kernel/sigtools.h"
#include <deque>

USING_YOSYS_NAMESPACE
PRIVATE_NAMESPACE_BEGIN

// DSPs whose P bits above the used ones are not the extension of the used
// bits (see prodModBits); such P must not feed a PCOUT -> PCIN cascade.
// Filled by xilinx_dsp_pack, consulted by the cascade matcher, cleared per
// module.
static pool<Cell*> inexact_p_dsps;

static bool dsp_p_inexact(Cell *cell)
{
	return inexact_p_dsps.count(cell) > 0;
}

// DSP48E1 (7-series) and DSP48E2 (UltraScale/UltraScale+) differ, for the
// purposes of packing, only in the pre-adder/multiplier operand width and in
// how the pre-adder is routed to the multiplier.
static bool dsp_is_e2(const Cell *cell)
{
	return cell->type == ID(DSP48E2);
}

static int dsp_preadd_width(const Cell *cell)
{
	return dsp_is_e2(cell) ? 27 : 25;
}

static bool dsp_preadd_used(const Cell *cell)
{
	if (dsp_is_e2(cell))
		return cell->getParam(ID(AMULTSEL)).decode_string() == "AD" ||
				cell->getParam("\\BMULTSEL").decode_string() == "AD";
	return cell->getParam(ID(USE_DPORT)).decode_string() == "TRUE";
}

// Range of the two's complement value of a DSP operand port, given that
// only its low n bits are significant (the rest being a sign or constant
// extension, see unextend()).
static void port_range(const SigSpec &full, int n, int64_t &lo, int64_t &hi)
{
	if (full.is_fully_const() && GetSize(full) <= 31) { // as_int_saturating is 32-bit
		lo = hi = full.as_const().as_int_saturating(true);
		return;
	}
	n = std::min(n, 62);
	if (n == 0) {
		lo = hi = 0;
	} else if (n == GetSize(full) || full[n].wire) {
		lo = -(int64_t(1) << (n-1));
		hi = (int64_t(1) << (n-1)) - 1;
	} else if (full[n] == State::S0) {
		lo = 0;
		hi = (int64_t(1) << n) - 1;
	} else {
		lo = -(int64_t(1) << n);
		hi = -1;
	}
}

// Range of an $add/$sub operand under the cell's signedness.
static void operand_range(const SigSpec &sig, bool is_signed, int64_t &lo, int64_t &hi)
{
	int n = std::min(GetSize(sig), 62);
	if (sig.is_fully_const() && GetSize(sig) <= 31) {
		lo = hi = sig.as_const().as_int_saturating(is_signed);
	} else if (n == 0) {
		lo = hi = 0;
	} else if (is_signed) {
		lo = -(int64_t(1) << (n-1));
		hi = (int64_t(1) << (n-1)) - 1;
	} else {
		lo = 0;
		hi = (int64_t(1) << n) - 1;
	}
}

// Exact range of the pre-adder result (only meaningful if preadd_exact()).
static void preadd_range(const SigMap &sigmap, Cell *add, int64_t &lo, int64_t &hi)
{
	bool sgn = add->getParam(ID::A_SIGNED).as_bool() && add->getParam(ID::B_SIGNED).as_bool();
	int64_t alo, ahi, blo, bhi;
	operand_range(sigmap(add->getPort(ID::A)), sgn, alo, ahi);
	operand_range(sigmap(add->getPort(ID::B)), sgn, blo, bhi);
	if (add->type == ID($sub)) {
		lo = alo - bhi;
		hi = ahi - blo;
	} else {
		lo = alo + blo;
		hi = ahi + bhi;
	}
}

// Can the DSP's exact product stand in for sigP as operand AB of the $add?
// sigP may be a truncation of the product; the DSP post-adder always adds the
// full product, so every result bit above sigP must be the extension the $add
// would apply to sigP, i.e. the product must fit sigP.
static bool postadd_product_exact(const SigMap &sigmap, Cell *add, IdString AB, const SigSpec &sigP,
		int64_t aLo, int64_t aHi, int64_t bLo, int64_t bHi)
{
	int W = GetSize(sigP);
	int Wy = GetSize(add->getPort(ID::Y));
	if (W == 0)
		return false;
	if (Wy <= W)
		return true;
	SigSpec op = sigmap(add->getPort(AB));
	int WA = GetSize(op);
	bool is_signed = add->getParam(ID::A_SIGNED).as_bool() && add->getParam(ID::B_SIGNED).as_bool();
	bool sign_ext = true, zero_ext = true;
	for (int i = W; i < Wy; i++) {
		SigBit b = i < WA ? op[i] : (is_signed ? op[WA-1] : SigBit(State::S0));
		if (b != sigP[W-1])
			sign_ext = false;
		if (b != State::S0)
			zero_ext = false;
	}
	if (W >= 62)
		return true;
	// operand ranges are below 2**62 in magnitude: products fit __int128
	__int128 c[4] = {(__int128)aLo*bLo, (__int128)aLo*bHi, (__int128)aHi*bLo, (__int128)aHi*bHi};
	__int128 lo = *std::min_element(c, c+4), hi = *std::max_element(c, c+4);
	log_debug("postadd_product_exact: W=%d Wy=%d signed=%d sign_ext=%d zero_ext=%d\n",
			W, Wy, is_signed, sign_ext, zero_ext);
	if (sign_ext && lo >= -((__int128)1 << (W-1)) && hi < ((__int128)1 << (W-1)))
		return true;
	if (zero_ext && lo >= 0 && hi < ((__int128)1 << W))
		return true;
	return false;
}

// The DSP pre-adder computes the exact (25/27-bit) sum/difference of its
// operands, extended per the $add/$sub signedness, and the multiplier then
// treats it as signed.  The RTL instead multiplies Y, truncated to its width
// and extended into the DSP A port either by sign or by zero.  Only fold when
// both views are the same number.
static bool preadd_exact(const SigMap &sigmap, Cell *add, const SigSpec &dspA)
{
	SigSpec Y = sigmap(add->getPort(ID::Y));
	int Wy = GetSize(Y);
	if (Wy == 0)
		return false;
	bool sgn = add->getParam(ID::A_SIGNED).as_bool() && add->getParam(ID::B_SIGNED).as_bool();
	int need = std::max(GetSize(add->getPort(ID::A)), GetSize(add->getPort(ID::B))) + 1;
	bool ext_sign = Wy >= GetSize(dspA) || dspA[Wy] == Y[Wy-1];
	bool ext_zero = Wy < GetSize(dspA) && dspA[Wy] == State::S0;
	if (Wy < need)
		return false;
	if (sgn)
		return ext_sign;
	if (add->type == ID($sub))
		return false; // unsigned difference may be negative
	// unsigned sum is non-negative and < 2**need
	return ext_zero || (ext_sign && Wy > need);
}

// Does the register have bits outside the argD slice whose D input is one of
// the argD bits (typically sign-extension copies)?
static bool ff_has_tail(const SigMap &sigmap, Cell *ff, const SigSpec &argD)
{
	pool<SigBit> bits(argD.begin(), argD.end());
	SigSpec D = sigmap(ff->getPort(ID::D));
	int n = 0;
	for (auto b : D)
		if (bits.count(b))
			n++;
	return n > GetSize(argD);
}

// Register bits whose D is one of the argD bits will be driven by the DSP
// output register after packing.  They must reset and power up like it: to
// zero (or undefined).
static bool out_dffe_tail_ok(const SigMap &sigmap, Cell *ff, const SigSpec &argD)
{
	pool<SigBit> bits(argD.begin(), argD.end());
	SigSpec D = sigmap(ff->getPort(ID::D));
	SigSpec Q = ff->getPort(ID::Q);
	bool has_srst = ff->type.in(ID($sdff), ID($sdffe));
	Const srst = has_srst ? ff->getParam(ID::SRST_VALUE) : Const();
	for (int k = 0; k < GetSize(D); k++) {
		if (!bits.count(D[k]))
			continue;
		if (has_srst && srst[k] != State::S0)
			return false;
		SigBit q = Q[k];
		if (!q.wire)
			return false;
		auto it = q.wire->attributes.find(ID::init);
		if (it != q.wire->attributes.end() && GetSize(it->second) > q.offset) {
			State s = it->second[q.offset];
			if (s != State::S0 && s != State::Sx)
				return false;
		}
	}
	return true;
}

// After an output register's bits `absorbedQ` have been moved into the DSP,
// rewire any remaining bits of that register that copied one of the absorbed
// D bits so they follow the corresponding DSP output instead of reading the
// (now undriven) pre-register signal.
static void rewire_ff_tail(const SigMap &sigmap, Module *module, Cell *ff, const SigSpec &absorbedQ)
{
	pool<SigBit> absorbed;
	for (auto b : sigmap(absorbedQ))
		absorbed.insert(b);
	SigSpec D = sigmap(ff->getPort(ID::D));
	SigSpec Q = ff->getPort(ID::Q);
	SigSpec mQ = sigmap(Q);
	dict<SigBit, SigBit> d2q;
	for (int k = 0; k < GetSize(Q); k++)
		if (absorbed.count(mQ[k]))
			d2q[D[k]] = Q[k];
	SigSpec newQ = Q;
	for (int k = 0; k < GetSize(Q); k++) {
		if (absorbed.count(mQ[k]))
			continue;
		auto it = d2q.find(D[k]);
		if (it == d2q.end())
			continue;
		module->connect(Q[k], it->second);
		newQ[k] = module->addWire(NEW_ID);
		// the copy now follows the DSP output register: drop its init
		if (Q[k].wire) {
			auto ia = Q[k].wire->attributes.find(ID::init);
			if (ia != Q[k].wire->attributes.end() && GetSize(ia->second) > Q[k].offset)
				ia->second.set(Q[k].offset, State::Sx);
		}
	}
	ff->setPort(ID::Q, newQ);
}

// Absorbed output register bits are now driven by the DSP; forget their
// (zero or undefined, checked by the matcher) init values.
static void clear_init(const SigSpec &absorbedQ)
{
	for (auto c : absorbedQ.chunks()) {
		if (!c.wire)
			continue;
		auto it = c.wire->attributes.find(ID::init);
		if (it == c.wire->attributes.end())
			continue;
		for (int i = c.offset; i < c.offset+c.width && i < GetSize(it->second); i++) {
			log_assert(it->second[i] == State::S0 || it->second[i] == State::Sx);
			it->second.set(i, State::Sx);
		}
	}
}

#include "techlibs/xilinx/xilinx_dsp_pm.h"
#include "techlibs/xilinx/xilinx_dsp48a_pm.h"
#include "techlibs/xilinx/xilinx_dsp_CREG_pm.h"
#include "techlibs/xilinx/xilinx_dsp_cascade_pm.h"

static Cell* addDsp(Module *module) {
	Cell *cell = module->addCell(NEW_ID, ID(DSP48E1));
	cell->setParam(ID(ACASCREG), 0);
	cell->setParam(ID(ADREG), 0);
	cell->setParam(ID(A_INPUT), Const("DIRECT"));
	cell->setParam(ID(ALUMODEREG), 0);
	cell->setParam(ID(AREG), 0);
	cell->setParam(ID(BCASCREG), 0);
	cell->setParam(ID(B_INPUT), Const("DIRECT"));
	cell->setParam(ID(BREG), 0);
	cell->setParam(ID(CARRYINREG), 0);
	cell->setParam(ID(CARRYINSELREG), 0);
	cell->setParam(ID(CREG), 0);
	cell->setParam(ID(DREG), 0);
	cell->setParam(ID(INMODEREG), 0);
	cell->setParam(ID(MREG), 0);
	cell->setParam(ID(OPMODEREG), 0);
	cell->setParam(ID(PREG), 0);
	cell->setParam(ID(USE_MULT), Const("NONE"));
	cell->setParam(ID(USE_SIMD), Const("ONE48"));
	cell->setParam(ID(USE_DPORT), Const("FALSE"));

	cell->setPort(ID::D, Const(0, 25));
	cell->setPort(ID(INMODE), Const(0, 5));
	cell->setPort(ID(ALUMODE), Const(0, 4));
	cell->setPort(ID(OPMODE), Const(0, 7));
	cell->setPort(ID(CARRYINSEL), Const(0, 3));
	cell->setPort(ID(ACIN), Const(0, 30));
	cell->setPort(ID(BCIN), Const(0, 18));
	cell->setPort(ID(PCIN), Const(0, 48));
	cell->setPort(ID(CARRYIN), Const(0, 1));
	return cell;
}

void xilinx_simd_pack(Module *module, const std::vector<Cell*> &selected_cells)
{
	std::deque<Cell*> simd12_add, simd12_sub;
	std::deque<Cell*> simd24_add, simd24_sub;

	for (auto cell : selected_cells) {
		if (!cell->type.in(ID($add), ID($sub)))
			continue;
		SigSpec Y = cell->getPort(ID::Y);
		if (!Y.is_chunk())
			continue;
		if (!Y.as_chunk().wire->get_strpool_attribute(ID(use_dsp)).count("simd"))
			continue;
		if (GetSize(Y) > 25)
			continue;
		SigSpec A = cell->getPort(ID::A);
		SigSpec B = cell->getPort(ID::B);
		if (GetSize(Y) <= 13) {
			if (GetSize(A) > 12)
				continue;
			if (GetSize(B) > 12)
				continue;
			if (cell->type == ID($add))
				simd12_add.push_back(cell);
			else if (cell->type == ID($sub))
				simd12_sub.push_back(cell);
		}
		else if (GetSize(Y) <= 25) {
			if (GetSize(A) > 24)
				continue;
			if (GetSize(B) > 24)
				continue;
			if (cell->type == ID($add))
				simd24_add.push_back(cell);
			else if (cell->type == ID($sub))
				simd24_sub.push_back(cell);
		}
		else
			log_abort();
	}

	auto f12 = [module](SigSpec &AB, SigSpec &C, SigSpec &P, SigSpec &CARRYOUT, Cell *lane) {
		SigSpec A = lane->getPort(ID::A);
		SigSpec B = lane->getPort(ID::B);
		SigSpec Y = lane->getPort(ID::Y);
		A.extend_u0(12, lane->getParam(ID::A_SIGNED).as_bool());
		B.extend_u0(12, lane->getParam(ID::B_SIGNED).as_bool());
		AB.append(A);
		C.append(B);
		if (GetSize(Y) < 13)
			Y.append(module->addWire(NEW_ID, 13-GetSize(Y)));
		else
			log_assert(GetSize(Y) == 13);
		P.append(Y.extract(0, 12));
		CARRYOUT.append(Y[12]);
	};
	auto g12 = [&f12,module](std::deque<Cell*> &simd12) {
		while (simd12.size() > 1) {
			SigSpec AB, C, P, CARRYOUT;

			Cell *lane1 = simd12.front();
			simd12.pop_front();
			Cell *lane2 = simd12.front();
			simd12.pop_front();
			Cell *lane3 = nullptr;
			Cell *lane4 = nullptr;

			if (!simd12.empty()) {
				lane3 = simd12.front();
				simd12.pop_front();
				if (!simd12.empty()) {
					lane4 = simd12.front();
					simd12.pop_front();
				}
			}

			log("Analysing %s.%s for Xilinx DSP SIMD12 packing.\n", module, lane1);

			Cell *cell = addDsp(module);
			cell->setParam(ID(USE_SIMD), Const("FOUR12"));
			// X = A:B
			// Y = 0
			// Z = C
			cell->setPort(ID(OPMODE), Const::from_string("0110011"));

			log_assert(lane1);
			log_assert(lane2);
			f12(AB, C, P, CARRYOUT, lane1);
			f12(AB, C, P, CARRYOUT, lane2);
			if (lane3) {
				f12(AB, C, P, CARRYOUT, lane3);
				if (lane4)
					f12(AB, C, P, CARRYOUT, lane4);
				else {
					AB.append(Const(0, 12));
					C.append(Const(0, 12));
					P.append(module->addWire(NEW_ID, 12));
					CARRYOUT.append(module->addWire(NEW_ID, 1));
				}
			}
			else {
				AB.append(Const(0, 24));
				C.append(Const(0, 24));
				P.append(module->addWire(NEW_ID, 24));
				CARRYOUT.append(module->addWire(NEW_ID, 2));
			}
			log_assert(GetSize(AB) == 48);
			log_assert(GetSize(C) == 48);
			log_assert(GetSize(P) == 48);
			log_assert(GetSize(CARRYOUT) == 4);
			cell->setPort(ID::A, AB.extract(18, 30));
			cell->setPort(ID::B, AB.extract(0, 18));
			cell->setPort(ID::C, C);
			cell->setPort(ID::P, P);
			cell->setPort(ID(CARRYOUT), CARRYOUT);
			if (lane1->type == ID($sub))
				cell->setPort(ID(ALUMODE), Const::from_string("0011"));

			module->remove(lane1);
			module->remove(lane2);
			if (lane3) module->remove(lane3);
			if (lane4) module->remove(lane4);

			module->design->select(module, cell);
		}
	};
	g12(simd12_add);
	g12(simd12_sub);

	auto f24 = [module](SigSpec &AB, SigSpec &C, SigSpec &P, SigSpec &CARRYOUT, Cell *lane) {
		SigSpec A = lane->getPort(ID::A);
		SigSpec B = lane->getPort(ID::B);
		SigSpec Y = lane->getPort(ID::Y);
		A.extend_u0(24, lane->getParam(ID::A_SIGNED).as_bool());
		B.extend_u0(24, lane->getParam(ID::B_SIGNED).as_bool());
		C.append(A);
		AB.append(B);
		if (GetSize(Y) < 25)
			Y.append(module->addWire(NEW_ID, 25-GetSize(Y)));
		else
			log_assert(GetSize(Y) == 25);
		P.append(Y.extract(0, 24));
		CARRYOUT.append(module->addWire(NEW_ID)); // TWO24 uses every other bit
		CARRYOUT.append(Y[24]);
	};
	auto g24 = [&f24,module](std::deque<Cell*> &simd24) {
		while (simd24.size() > 1) {
			SigSpec AB;
			SigSpec C;
			SigSpec P;
			SigSpec CARRYOUT;

			Cell *lane1 = simd24.front();
			simd24.pop_front();
			Cell *lane2 = simd24.front();
			simd24.pop_front();

			log("Analysing %s.%s for Xilinx DSP SIMD24 packing.\n", module, lane1);

			Cell *cell = addDsp(module);
			cell->setParam(ID(USE_SIMD), Const("TWO24"));
			// X = A:B
			// Y = 0
			// Z = C
			cell->setPort(ID(OPMODE), Const::from_string("0110011"));

			log_assert(lane1);
			log_assert(lane2);
			f24(AB, C, P, CARRYOUT, lane1);
			f24(AB, C, P, CARRYOUT, lane2);
			log_assert(GetSize(AB) == 48);
			log_assert(GetSize(C) == 48);
			log_assert(GetSize(P) == 48);
			log_assert(GetSize(CARRYOUT) == 4);
			cell->setPort(ID::A, AB.extract(18, 30));
			cell->setPort(ID::B, AB.extract(0, 18));
			cell->setPort(ID::C, C);
			cell->setPort(ID::P, P);
			cell->setPort(ID(CARRYOUT), CARRYOUT);
			if (lane1->type == ID($sub))
				cell->setPort(ID(ALUMODE), Const::from_string("0011"));

			module->remove(lane1);
			module->remove(lane2);

			module->design->select(module, cell);
		}
	};
	g24(simd24_add);
	g24(simd24_sub);
}

void xilinx_dsp_pack(xilinx_dsp_pm &pm)
{
	auto &st = pm.st_xilinx_dsp_pack;

	log("Analysing %s.%s for Xilinx DSP packing.\n", pm.module, st.dsp);

	log_debug("preAdd:     %s\n", st.preAdd ? st.preAdd->name.unescape() : "--");
	log_debug("preSub:     %s\n", st.preSub ? st.preSub->name.unescape() : "--");
	log_debug("ffAD:       %s\n", st.ffAD ? st.ffAD->name.unescape() : "--");
	log_debug("ffA2:       %s\n", st.ffA2 ? st.ffA2->name.unescape() : "--");
	log_debug("ffA1:       %s\n", st.ffA1 ? st.ffA1->name.unescape() : "--");
	log_debug("ffB2:       %s\n", st.ffB2 ? st.ffB2->name.unescape() : "--");
	log_debug("ffB1:       %s\n", st.ffB1 ? st.ffB1->name.unescape() : "--");
	log_debug("ffD:        %s\n", st.ffD ? st.ffD->name.unescape() : "--");
	log_debug("dsp:        %s\n", st.dsp ? st.dsp->name.unescape() : "--");
	log_debug("ffM:        %s\n", st.ffM ? st.ffM->name.unescape() : "--");
	log_debug("postAdd:    %s\n", st.postAdd ? st.postAdd->name.unescape() : "--");
	log_debug("postAddMux: %s\n", st.postAddMux ? st.postAddMux->name.unescape() : "--");
	log_debug("ffP:        %s\n", st.ffP ? st.ffP->name.unescape() : "--");
	log_debug("overflow:   %s\n", st.overflow ? st.overflow->name.unescape() : "--");

	Cell *cell = st.dsp;

	if (st.preAdd || st.preSub) {
		Cell* preAdder = st.preAdd ? st.preAdd : st.preSub;

		log("  preadder %s (%s)\n", preAdder, preAdder->type.unescape());
		bool A_SIGNED = preAdder->getParam(ID::A_SIGNED).as_bool();
		bool D_SIGNED = preAdder->getParam(ID::B_SIGNED).as_bool();
		if (st.sigA == preAdder->getPort(ID::B))
			std::swap(A_SIGNED, D_SIGNED);
		st.sigA.extend_u0(30, A_SIGNED);
		st.sigD.extend_u0(dsp_preadd_width(cell), D_SIGNED);
		cell->setPort(ID::A, st.sigA);
		cell->setPort(ID::D, st.sigD);
		if (preAdder->type == ID($add))
			cell->setPort(ID(INMODE), Const::from_string("00100"));
		else
			cell->setPort(ID(INMODE), Const::from_string("01100"));

		if (st.ffAD) {
			if (st.ffAD->type.in(ID($dffe), ID($sdffe))) {
				bool pol = st.ffAD->getParam(ID::EN_POLARITY).as_bool();
				SigSpec S = st.ffAD->getPort(ID::EN);
				cell->setPort(ID(CEAD), pol ? S : pm.module->Not(NEW_ID, S));
			}
			else
				cell->setPort(ID(CEAD), State::S1);
			cell->setParam(ID(ADREG), 1);
		}

		if (dsp_is_e2(cell))
			cell->setParam(ID(AMULTSEL), Const("AD"));
		else
			cell->setParam(ID(USE_DPORT), Const("TRUE"));

		pm.autoremove(preAdder);
	}
	if (st.postAdd) {
		log("  postadder %s (%s)\n", st.postAdd, st.postAdd->type.unescape());

		SigSpec &opmode = cell->connections_.at(ID(OPMODE));
		if (st.postAddMux) {
			log_assert(st.ffP);
			opmode[4] = st.postAddMux->getPort(ID::S);
			pm.autoremove(st.postAddMux);
		}
		else if (st.ffP && st.sigC == st.sigP)
			opmode[4] = State::S0;
		else
			opmode[4] = State::S1;
		opmode[6] = State::S0;
		opmode[5] = State::S1;

		if (opmode[4] != State::S0) {
			if (st.postAddMuxAB == ID::A)
				st.sigC.extend_u0(48, st.postAdd->getParam(ID::B_SIGNED).as_bool());
			else
				st.sigC.extend_u0(48, st.postAdd->getParam(ID::A_SIGNED).as_bool());
			cell->setPort(ID::C, st.sigC);
		}

		pm.autoremove(st.postAdd);
	}
	if (st.overflow) {
		log("  overflow %s (%s)\n", st.overflow, st.overflow->type.unescape());
		cell->setParam(ID(USE_PATTERN_DETECT), Const("PATDET"));
		cell->setParam(ID(SEL_PATTERN), Const("PATTERN"));
		cell->setParam(ID(SEL_MASK), Const("MASK"));

		if (st.overflow->type == ID($ge)) {
			Const B = st.overflow->getPort(ID::B).as_const();
			log_assert(std::count(B.begin(), B.end(), State::S1) == 1);
			// Since B is an exact power of 2, subtract 1
			//   by inverting all bits up until hitting
			//   that one hi bit
			for (auto b : B)
				if (b == State::S0) b = State::S1;
				else if (b == State::S1) {
					b = State::S0;
					break;
				}
			B.extu(48);

			cell->setParam(ID(MASK), B);
			cell->setParam(ID(PATTERN), Const(0, 48));
			cell->setPort(ID(OVERFLOW), st.overflow->getPort(ID::Y));
		}
		else log_abort();

		pm.autoremove(st.overflow);
	}

	if (st.clock != SigBit())
	{
		cell->setPort(ID::CLK, st.clock);

		auto f = [&pm,cell](SigSpec &A, Cell* ff, IdString ceport, IdString rstport) {
			SigSpec D = ff->getPort(ID::D);
			SigSpec Q = pm.sigmap(ff->getPort(ID::Q));
			if (!A.empty())
				A.replace(Q, D);
			if (rstport != IdString::Null) {
				if (ff->type.in(ID($sdff), ID($sdffe))) {
					SigSpec srst = ff->getPort(ID::SRST);
					bool rstpol = ff->getParam(ID::SRST_POLARITY).as_bool();
					cell->setPort(rstport, rstpol ? srst : pm.module->Not(NEW_ID, srst));
				} else {
					cell->setPort(rstport, State::S0);
				}
			}
			if (ff->type.in(ID($dffe), ID($sdffe))) {
				SigSpec ce = ff->getPort(ID::EN);
				bool cepol = ff->getParam(ID::EN_POLARITY).as_bool();
				cell->setPort(ceport, cepol ? ce : pm.module->Not(NEW_ID, ce));
			}
			else
				cell->setPort(ceport, State::S1);
			// Input registers stay in the fabric (possibly with other
			// users, e.g. #6046) and keep their init values; output
			// registers are handled via clear_init() below.
		};

		if (st.ffA2) {
			SigSpec A = cell->getPort(ID::A);
			f(A, st.ffA2, ID(CEA2), ID(RSTA));
			if (st.ffA1) {
				f(A, st.ffA1, ID(CEA1), IdString::Null);
				cell->setParam(ID(AREG), 2);
				cell->setParam(ID(ACASCREG), 2);
			}
			else {
				cell->setParam(ID(AREG), 1);
				cell->setParam(ID(ACASCREG), 1);
			}
			pm.add_siguser(A, cell);
			cell->setPort(ID::A, A);
		}
		if (st.ffB2) {
			SigSpec B = cell->getPort(ID::B);
			f(B, st.ffB2, ID(CEB2), ID(RSTB));
			if (st.ffB1) {
				f(B, st.ffB1, ID(CEB1), IdString::Null);
				cell->setParam(ID(BREG), 2);
				cell->setParam(ID(BCASCREG), 2);
			}
			else {
				cell->setParam(ID(BREG), 1);
				cell->setParam(ID(BCASCREG), 1);
			}
			pm.add_siguser(B, cell);
			cell->setPort(ID::B, B);
		}
		if (st.ffD) {
			SigSpec D = cell->getPort(ID::D);
			f(D, st.ffD, ID(CED), ID(RSTD));
			pm.add_siguser(D, cell);
			cell->setPort(ID::D, D);
			cell->setParam(ID(DREG), 1);
		}
		if (st.ffM) {
			SigSpec M; // unused
			f(M, st.ffM, ID(CEM), ID(RSTM));
			clear_init(st.sigM);
			st.ffM->connections_.at(ID::Q).replace(st.sigM, pm.module->addWire(NEW_ID, GetSize(st.sigM)));
			cell->setParam(ID(MREG), State::S1);
		}
		if (st.ffP) {
			SigSpec P; // unused
			f(P, st.ffP, ID(CEP), ID(RSTP));
			clear_init(st.sigP);
			rewire_ff_tail(pm.sigmap, pm.module, st.ffP, st.sigP);
			st.ffP->connections_.at(ID::Q).replace(st.sigP, pm.module->addWire(NEW_ID, GetSize(st.sigP)));
			cell->setParam(ID(PREG), State::S1);
		}

		log("  clock: %s (%s)\n", log_signal(st.clock), "posedge");

		if (st.ffA2) {
			log(" ffA2:%s\n", st.ffA2);
			if (st.ffA1)
				log(" ffA1:%s\n", st.ffA1);
		}

		if (st.ffAD)
			log(" ffAD:%s\n", st.ffAD);

		if (st.ffB2) {
			log(" ffB2:%s\n", st.ffB2);
			if (st.ffB1)
				log(" ffB1:%s\n", st.ffB1);
		}

		if (st.ffD)
			log(" ffD:%s\n", st.ffD);

		if (st.ffM)
			log(" ffM:%s\n", st.ffM);

		if (st.ffP)
			log(" ffP:%s\n", st.ffP);
	}
	log("\n");

	SigSpec P = st.sigP;
	if (GetSize(P) < 48)
		P.append(pm.module->addWire(NEW_ID, 48-GetSize(P)));
	cell->setPort(ID::P, P);

	// Upper P bits are not the extension of the used bits: P must not be
	// forwarded wholesale through PCOUT -> PCIN.
	if (st.prodModBits < 48)
		inexact_p_dsps.insert(cell);

	pm.blacklist(cell);
}

void xilinx_dsp48a_pack(xilinx_dsp48a_pm &pm)
{
	auto &st = pm.st_xilinx_dsp48a_pack;

	log("Analysing %s.%s for Xilinx DSP48A/DSP48A1 packing.\n", pm.module, st.dsp);

	log_debug("preAdd:     %s\n", st.preAdd ? st.preAdd->name.unescape() : "--");
	log_debug("ffA1:       %s\n", st.ffA1 ? st.ffA1->name.unescape() : "--");
	log_debug("ffA0:       %s\n", st.ffA0 ? st.ffA0->name.unescape() : "--");
	log_debug("ffB1:       %s\n", st.ffB1 ? st.ffB1->name.unescape() : "--");
	log_debug("ffB0:       %s\n", st.ffB0 ? st.ffB0->name.unescape() : "--");
	log_debug("ffD:        %s\n", st.ffD ? st.ffD->name.unescape() : "--");
	log_debug("dsp:        %s\n", st.dsp ? st.dsp->name.unescape() : "--");
	log_debug("ffM:        %s\n", st.ffM ? st.ffM->name.unescape() : "--");
	log_debug("postAdd:    %s\n", st.postAdd ? st.postAdd->name.unescape() : "--");
	log_debug("postAddMux: %s\n", st.postAddMux ? st.postAddMux->name.unescape() : "--");
	log_debug("ffP:        %s\n", st.ffP ? st.ffP->name.unescape() : "--");

	Cell *cell = st.dsp;
	SigSpec &opmode = cell->connections_.at(ID(OPMODE));

	if (st.preAdd) {
		log("  preadder %s (%s)\n", st.preAdd, st.preAdd->type.unescape());
		bool D_SIGNED = st.preAdd->getParam(ID::A_SIGNED).as_bool();
		bool B_SIGNED = st.preAdd->getParam(ID::B_SIGNED).as_bool();
		st.sigB.extend_u0(18, B_SIGNED);
		st.sigD.extend_u0(18, D_SIGNED);
		cell->setPort(ID::B, st.sigB);
		cell->setPort(ID::D, st.sigD);
		opmode[4] = State::S1;
		if (st.preAdd->type == ID($add))
			opmode[6] = State::S0;
		else if (st.preAdd->type == ID($sub))
			opmode[6] = State::S1;
		else
			log_assert(!"strange pre-adder type");

		pm.autoremove(st.preAdd);
	}
	if (st.postAdd) {
		log("  postadder %s (%s)\n", st.postAdd, st.postAdd->type.unescape());

		if (st.postAddMux) {
			log_assert(st.ffP);
			opmode[2] = st.postAddMux->getPort(ID::S);
			pm.autoremove(st.postAddMux);
		}
		else if (st.ffP && st.sigC == st.sigP)
			opmode[2] = State::S0;
		else
			opmode[2] = State::S1;
		opmode[3] = State::S1;

		if (opmode[2] != State::S0) {
			if (st.postAddMuxAB == ID::A)
				st.sigC.extend_u0(48, st.postAdd->getParam(ID::B_SIGNED).as_bool());
			else
				st.sigC.extend_u0(48, st.postAdd->getParam(ID::A_SIGNED).as_bool());
			cell->setPort(ID::C, st.sigC);
		}

		pm.autoremove(st.postAdd);
	}

	if (st.clock != SigBit())
	{
		cell->setPort(ID::CLK, st.clock);

		auto f = [&pm,cell](SigSpec &A, Cell* ff, IdString ceport, IdString rstport) {
			SigSpec D = ff->getPort(ID::D);
			SigSpec Q = pm.sigmap(ff->getPort(ID::Q));
			if (!A.empty())
				A.replace(Q, D);
			if (rstport != IdString::Null) {
				if (ff->type.in(ID($sdff), ID($sdffe))) {
					SigSpec srst = ff->getPort(ID::SRST);
					bool rstpol = ff->getParam(ID::SRST_POLARITY).as_bool();
					cell->setPort(rstport, rstpol ? srst : pm.module->Not(NEW_ID, srst));
				} else {
					cell->setPort(rstport, State::S0);
				}
			}
			if (ff->type.in(ID($dffe), ID($sdffe))) {
				SigSpec ce = ff->getPort(ID::EN);
				bool cepol = ff->getParam(ID::EN_POLARITY).as_bool();
				cell->setPort(ceport, cepol ? ce : pm.module->Not(NEW_ID, ce));
			}
			else
				cell->setPort(ceport, State::S1);

			for (auto c : Q.chunks()) {
				auto it = c.wire->attributes.find(ID::init);
				if (it == c.wire->attributes.end())
					continue;
				for (int i = c.offset; i < c.offset+c.width; i++) {
					log_assert(it->second[i] == State::S0 || it->second[i] == State::Sx);
					it->second.set(i, State::Sx);
				}
			}
		};

		if (st.ffA0 || st.ffA1) {
			SigSpec A = cell->getPort(ID::A);
			if (st.ffA1) {
				f(A, st.ffA1, ID(CEA), ID(RSTA));
				cell->setParam(ID(A1REG), 1);
			}
			if (st.ffA0) {
				f(A, st.ffA0, ID(CEA), ID(RSTA));
				cell->setParam(ID(A0REG), 1);
			}
			pm.add_siguser(A, cell);
			cell->setPort(ID::A, A);
		}
		if (st.ffB0 || st.ffB1) {
			SigSpec B = cell->getPort(ID::B);
			if (st.ffB1) {
				f(B, st.ffB1, ID(CEB), ID(RSTB));
				cell->setParam(ID(B1REG), 1);
			}
			if (st.ffB0) {
				f(B, st.ffB0, ID(CEB), ID(RSTB));
				cell->setParam(ID(B0REG), 1);
			}
			pm.add_siguser(B, cell);
			cell->setPort(ID::B, B);
		}
		if (st.ffD) {
			SigSpec D = cell->getPort(ID::D);
			f(D, st.ffD, ID(CED), ID(RSTD));
			pm.add_siguser(D, cell);
			cell->setPort(ID::D, D);
			cell->setParam(ID(DREG), 1);
		}
		if (st.ffM) {
			SigSpec M; // unused
			f(M, st.ffM, ID(CEM), ID(RSTM));
			st.ffM->connections_.at(ID::Q).replace(st.sigM, pm.module->addWire(NEW_ID, GetSize(st.sigM)));
			cell->setParam(ID(MREG), State::S1);
		}
		if (st.ffP) {
			SigSpec P; // unused
			f(P, st.ffP, ID(CEP), ID(RSTP));
			st.ffP->connections_.at(ID::Q).replace(st.sigP, pm.module->addWire(NEW_ID, GetSize(st.sigP)));
			cell->setParam(ID(PREG), State::S1);
		}

		std::string message = stringf("  clock: %s (%s)", log_signal(st.clock), "posedge");

		if (st.ffA0)
			message += stringf(" ffA0:%s", st.ffA0);
		if (st.ffA1)
			message += stringf(" ffA1:%s", st.ffA1);

		if (st.ffB0)
			message += stringf(" ffB0:%s", st.ffB0);
		if (st.ffB1)
			message += stringf(" ffB1:%s", st.ffB1);

		if (st.ffD)
			message += stringf(" ffD:%s", st.ffD);

		if (st.ffM)
			message += stringf(" ffM:%s", st.ffM);

		if (st.ffP)
			message += stringf(" ffP:%s", st.ffP);

		log("%s\n", message);
	}

	SigSpec P = st.sigP;
	if (GetSize(P) < 48)
		P.append(pm.module->addWire(NEW_ID, 48-GetSize(P)));
	cell->setPort(ID::P, P);

	pm.blacklist(cell);
}

void xilinx_dsp_packC(xilinx_dsp_CREG_pm &pm)
{
	auto &st = pm.st_xilinx_dsp_packC;

	log_debug("Analysing %s.%s for Xilinx DSP packing (CREG).\n", pm.module, st.dsp);
	log_debug("ffC:        %s\n", st.ffC ? st.ffC->name.unescape() : "--");

	Cell *cell = st.dsp;

	if (st.clock != SigBit())
	{
		cell->setPort(ID::CLK, st.clock);

		auto f = [&pm,cell](SigSpec &A, Cell* ff, IdString ceport, IdString rstport) {
			SigSpec D = ff->getPort(ID::D);
			SigSpec Q = pm.sigmap(ff->getPort(ID::Q));
			if (!A.empty())
				A.replace(Q, D);
			if (rstport != IdString::Null) {
				if (ff->type.in(ID($sdff), ID($sdffe))) {
					SigSpec srst = ff->getPort(ID::SRST);
					bool rstpol = ff->getParam(ID::SRST_POLARITY).as_bool();
					cell->setPort(rstport, rstpol ? srst : pm.module->Not(NEW_ID, srst));
				} else {
					cell->setPort(rstport, State::S0);
				}
			}
			if (ff->type.in(ID($dffe), ID($sdffe))) {
				SigSpec ce = ff->getPort(ID::EN);
				bool cepol = ff->getParam(ID::EN_POLARITY).as_bool();
				cell->setPort(ceport, cepol ? ce : pm.module->Not(NEW_ID, ce));
			}
			else
				cell->setPort(ceport, State::S1);

			for (auto c : Q.chunks()) {
				auto it = c.wire->attributes.find(ID::init);
				if (it == c.wire->attributes.end())
					continue;
				for (int i = c.offset; i < c.offset+c.width; i++) {
					log_assert(it->second[i] == State::S0 || it->second[i] == State::Sx);
					it->second.set(i, State::Sx);
				}
			}
		};

		if (st.ffC) {
			SigSpec C = cell->getPort(ID::C);
			f(C, st.ffC, ID(CEC), ID(RSTC));
			pm.add_siguser(C, cell);
			cell->setPort(ID::C, C);
			cell->setParam(ID(CREG), 1);
		}

		std::string message = stringf("  clock: %s (%s)", log_signal(st.clock), "posedge");

		if (st.ffC)
			message += stringf(" ffC:%s", st.ffC);
		log("%s\n", message);
	}

	pm.blacklist(cell);
}

struct XilinxDspPass : public Pass {
	XilinxDspPass() : Pass("xilinx_dsp", "Xilinx: pack resources into DSPs") { }
	void help() override
	{
		//   |---v---|---v---|---v---|---v---|---v---|---v---|---v---|---v---|---v---|---v---|
		log("\n");
		log("    xilinx_dsp [options] [selection]\n");
		log("\n");
		log("Pack input registers (A2, A1, B2, B1, C, D, AD; with optional enable/reset),\n");
		log("pipeline registers (M; with optional enable/reset), output registers (P; with\n");
		log("optional enable/reset), pre-adder and/or post-adder into Xilinx DSP resources.\n");
		log("\n");
		log("Multiply-accumulate operations using the post-adder with feedback on the 'C'\n");
		log("input will be folded into the DSP. In this scenario only, the 'C' input can be\n");
		log("used to override the current accumulation result with a new value, which will\n");
		log("be added to the multiplier result to form the next accumulation result.\n");
		log("\n");
		log("Use of the dedicated 'PCOUT' -> 'PCIN' cascade path is detected for 'P' -> 'C'\n");
		log("connections (optionally, where 'P' is right-shifted by 17-bits and used as an\n");
		log("input to the post-adder -- a pattern common for summing partial products to\n");
		log("implement wide multipliers). Limited support also exists for similar cascading\n");
		log("for A and B using '[AB]COUT' -> '[AB]CIN'. Currently, cascade chains are limited\n");
		log("to a maximum length of 20 cells, corresponding to the smallest Xilinx 7 Series\n");
		log("device.\n");
		log("\n");
		log("This pass is a no-op if the scratchpad variable 'xilinx_dsp.multonly' is set\n");
		log("to 1.\n");
		log("\n");
		log("\n");
		log("Experimental feature: addition/subtractions less than 12 or 24 bits with the\n");
		log("'(* use_dsp=\"simd\" *)' attribute attached to the output wire or attached to\n");
		log("the add/subtract operator will cause those operations to be implemented using\n");
		log("the 'SIMD' feature of DSPs.\n");
		log("\n");
		log("Experimental feature: the presence of a `$ge' cell attached to the registered\n");
		log("P output implementing the operation \"(P >= <power-of-2>)\" will be transformed\n");
		log("into using the DSP48E1's pattern detector feature for overflow detection.\n");
		log("\n");
		log("    -family {xcup|xcu|xc7|xc6v|xc5v|xc4v|xc6s|xc3sda}\n");
		log("        select the family to target\n");
		log("        default: xc7\n");
		log("\n");
	}
	void execute(std::vector<std::string> args, RTLIL::Design *design) override
	{
		log_header(design, "Executing XILINX_DSP pass (pack resources into DSPs).\n");

		std::string family = "xc7";
		size_t argidx;
		for (argidx = 1; argidx < args.size(); argidx++)
		{
			if ((args[argidx] == "-family" || args[argidx] == "-arch") && argidx+1 < args.size()) {
				family = args[++argidx];
				continue;
			}
			break;
		}
		extra_args(args, argidx, design);

		// Don't bother distinguishing between those.
		if (family == "xc6v")
			family = "xc7";
		if (family == "xcup")
			family = "xcu";

		for (auto module : design->selected_modules()) {

			if (design->scratchpad_get_bool("xilinx_dsp.multonly"))
				continue;

			// Experimental feature: pack $add/$sub cells with
			//   (* use_dsp48="simd" *) into DSP48E1's using its
			//   SIMD feature
			if (family == "xc7")
				xilinx_simd_pack(module, module->selected_cells());

			// Match for all features ([ABDMP][12]?REG, pre-adder,
			// post-adder, pattern detector, etc.) except for CREG
			if (family == "xc7" || family == "xcu") {
				xilinx_dsp_pm pm(module, module->selected_cells());
				pm.run_xilinx_dsp_pack(xilinx_dsp_pack);
			} else if (family == "xc6s" || family == "xc3sda") {
				xilinx_dsp48a_pm pm(module, module->selected_cells());
				pm.run_xilinx_dsp48a_pack(xilinx_dsp48a_pack);
			}
			// Separating out CREG packing is necessary since there
			//   is no guarantee that the cell ordering corresponds
			//   to the "expected" case (i.e. the order in which
			//   they appear in the source) thus the possiblity
			//   existed that a register got packed as a CREG into a
			//   downstream DSP that should have otherwise been a
			//   PREG of an upstream DSP that had not been visited
			//   yet
			{
				xilinx_dsp_CREG_pm pm(module, module->selected_cells());
				pm.run_xilinx_dsp_packC(xilinx_dsp_packC);
			}
			// Lastly, identify and utilise PCOUT -> PCIN,
			//   ACOUT -> ACIN, and BCOUT-> BCIN dedicated cascade
			//   chains
			{
				xilinx_dsp_cascade_pm pm(module, module->selected_cells());
				pm.run_xilinx_dsp_cascade();
			}
			inexact_p_dsps.clear();
		}
	}
} XilinxDspPass;

PRIVATE_NAMESPACE_END
